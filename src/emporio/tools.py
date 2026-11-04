"""Tools the model can call. Each returns a plain, JSON-serialisable dict.

Design rules:
- Prices, stock, dates and eligibility are computed here, never by the model.
- Order data is only returned after the customer proves ownership with the
  phone or e-mail on file (LGPD). A failed check never reveals whether an
  order number exists.
- Errors are returned as data ({"erro": ...}) so the model can recover and
  explain, instead of the conversation crashing.
"""
from __future__ import annotations

import inspect
import json
import re
import secrets
from datetime import timedelta
from decimal import Decimal

from .config import Settings
from .data import Customer, Order, Product, Store, norm
from .policies import SECTION_KEYS, Policies
from .pricing import brl, money, quote

# Words a customer uses for each category (accent-free, singular and plural).
CATEGORY_WORDS = {
    1: {"guitarra", "guitarras"},
    2: {"baixo", "baixos", "contrabaixo eletrico"},
    3: {"bateria", "baterias", "percussao"},
    4: {"teclado", "teclados", "piano", "pianos", "sintetizador", "sintetizadores"},
    5: {"violao", "violoes"},
    6: {"flauta", "flautas", "clarinete", "clarinetes", "sax", "saxofone", "saxofones", "oboe", "sopro"},
    7: {"trompete", "trompetes", "trombone", "trombones", "tuba", "tubas", "trompa", "trompas"},
    8: {"violino", "violinos", "viola", "violas", "violoncelo", "violoncelos", "contrabaixo acustico"},
    9: {"ukulele", "ukuleles", "uke"},
}
STOPWORDS = {
    "de", "do", "da", "dos", "das", "com", "para", "pra", "o", "a", "os", "as", "um", "uma", "e", "ou",
    "em", "no", "na", "que", "tem", "voces", "algum", "alguma", "quero", "modelo", "modelos",
    "instrumento", "instrumentos", "opcao", "opcoes", "barato", "barata", "baratos", "bom", "boa",
}
# A few spoken forms that differ from how the catalogue names things.
SYNONYMS = {"eletroacustico": "eletrico", "eletroacustica": "eletrico", "eletrica": "eletrico", "acustico": ""}

def catalogue_mentions(text: str, store: Store) -> dict[str, str]:
    """Instrument categories and brands named in a message.

    Maps each word found to the text that proves it was looked up: the category's
    name for category words ('violão' -> 'violoes'), the brand itself for brands.
    """
    query = norm(text)
    found = {}
    for category_id, words in CATEGORY_WORDS.items():
        for word in words:
            if re.search(rf"\b{re.escape(word)}\b", query):
                found[word] = norm(store.category_name(category_id))
    brands = {norm(p.name).split()[0] for p in store.products.values()} - {"music", "bateria", "teclado"}
    for brand in brands:
        if re.search(rf"\b{re.escape(brand)}\b", query):
            found[brand] = brand
    return found


def mentions_catalogue(text: str, store: Store) -> bool:
    return bool(catalogue_mentions(text, store))


ORDER_STATUS = {
    "pending": "pendente",
    "confirmed": "confirmado (ainda sem envio)",
    "shipped": "enviado (em transporte)",
    "delivered": "entregue",
    "cancelled": "cancelado",
}


def _payment_pt(method: str) -> str:
    credit = re.fullmatch(r"credit_(\d+)x", method)
    if credit:
        return f"cartão de crédito em {credit.group(1)}x"
    return {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}.get(method, method)


class Toolbox:
    def __init__(self, store: Store, policies: Policies, settings: Settings):
        self.store = store
        self.policies = policies
        self.settings = settings
        self._registry = {
            "search_products": self.search_products,
            "get_product_details": self.get_product_details,
            "list_active_promotions": self.list_active_promotions,
            "get_policy": self.get_policy,
            "lookup_orders": self.lookup_orders,
            "check_return_options": self.check_return_options,
            "escalate_to_human": self.escalate_to_human,
        }

    # ------------------------------------------------------------------ dispatch
    def call(self, name: str, arguments: dict | str | None) -> dict:
        fn = self._registry.get(name)
        if fn is None:
            return {"erro": f"ferramenta desconhecida: {name}"}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                return {"erro": "argumentos não são JSON válido"}
        arguments = arguments or {}
        accepted = inspect.signature(fn).parameters
        kwargs = {k: v for k, v in arguments.items() if k in accepted and v not in (None, "")}
        try:
            return fn(**kwargs)
        except (TypeError, ValueError) as exc:
            return {"erro": f"argumentos inválidos para {name}: {exc}"}

    # ------------------------------------------------------------------ catalogue
    def _effective_price(self, product: Product) -> Decimal:
        promo = self.store.active_promotion(product.id)
        if promo is None:
            return product.price
        return money(product.price * (Decimal(100) - promo.discount_percent) / Decimal(100))

    def _summary(self, product: Product) -> dict:
        promo = self.store.active_promotion(product.id)
        item = {
            "id": product.id,
            "nome": product.name,
            "categoria": self.store.category_name(product.category_id),
            "preco": brl(self._effective_price(product)),
            "disponibilidade": product.availability,
        }
        if promo:
            item["preco_original"] = brl(product.price)
            item["promocao"] = f"{promo.discount_percent}% off — {promo.description}"
        if product.available and product.stock <= 2:
            item["observacao"] = "últimas unidades"
        return item

    def _parse_query(self, text: str) -> tuple[set[int], list[str]]:
        query = norm(text)
        categories = set()
        for category_id, words in CATEGORY_WORDS.items():
            for word in words:
                if re.search(rf"\b{re.escape(word)}\b", query):
                    categories.add(category_id)
                    query = re.sub(rf"\b{re.escape(word)}\b", " ", query)
        tokens = []
        for token in query.split():
            token = SYNONYMS.get(token, token)
            if token and token not in STOPWORDS:
                tokens.append(token)
        return categories, tokens

    def _haystack(self, product: Product) -> str:
        specs = " ".join(str(v) for v in product.specs.values())
        return norm(f"{product.name} {product.description} {specs}")

    def search_products(
        self,
        termo: str | None = None,
        categoria: str | None = None,
        preco_max: float | None = None,
        preco_min: float | None = None,
        incluir_indisponiveis: bool = False,
    ) -> dict:
        categories, tokens = self._parse_query(f"{termo or ''} {categoria or ''}")
        candidates = []
        for product in self.store.products.values():
            if categories and product.category_id not in categories:
                continue
            if not incluir_indisponiveis and not product.available:
                continue
            price = self._effective_price(product)
            if preco_max is not None and price > money(preco_max):
                continue
            if preco_min is not None and price < money(preco_min):
                continue
            candidates.append(product)

        matched = [p for p in candidates if all(t in self._haystack(p) for t in tokens)]
        partial = False
        if tokens and not matched:
            matched = [p for p in candidates if any(t in self._haystack(p) for t in tokens)]
            partial = bool(matched)

        matched.sort(key=self._effective_price)
        result = {
            "criterio_de_preco": "preço final, já com promoção ativa; desconto PIX não incluído",
            "total_encontrado": len(matched),
            "produtos": [self._summary(p) for p in matched[:12]],
        }
        if len(matched) > 12:
            result["aviso"] = f"mostrando 12 de {len(matched)}; refine a busca"
        if partial:
            result["aviso_correspondencia"] = "nenhum produto tinha todos os termos; estes têm parte deles"
        if tokens and not incluir_indisponiveis:
            # A named model that exists but can't be sold: say so explicitly instead of letting
            # the model infer it from an empty or partial result.
            unavailable = [
                p for p in self.store.products.values()
                if not p.available and (not categories or p.category_id in categories)
                and all(t in self._haystack(p) for t in tokens)
            ]
            if unavailable:
                result["indisponiveis_com_esse_nome"] = [
                    {"nome": p.name, "disponibilidade": p.availability} for p in unavailable[:5]
                ]
        empty = [c for c in categories if not any(p.category_id == c for p in self.store.products.values())]
        if empty:
            names = ", ".join(self.store.category_name(c) for c in empty)
            # Worded so it can't be misread as "we have some of these" (it was, in an example run).
            result["categorias_sem_produtos"] = (
                f"Nenhum produto à venda em {names}. A loja não tem nenhum instrumento dessa categoria "
                f"disponível agora: não cite modelos nem tipos de instrumento dessa categoria."
            )
        if not matched and not empty:
            result["dica"] = "nada encontrado; tente sem filtros de preço ou com incluir_indisponiveis=true"
        return result

    def _resolve_product(self, query: str) -> Product | dict:
        query = str(query).strip()
        if query.isdigit() and int(query) in self.store.products:
            return self.store.products[int(query)]
        tokens = [t for t in norm(query).split() if t not in STOPWORDS]
        if not tokens:
            return {"erro": "informe o nome ou modelo do produto"}

        def score(product: Product) -> float:
            name = norm(product.name)
            words = name.split()
            return sum(1.0 if t in words else 0.5 if t in name else 0.0 for t in tokens) / len(tokens)

        ranked = sorted(self.store.products.values(), key=score, reverse=True)
        best = score(ranked[0])
        if best < 0.5:
            return {"erro": f"não encontrei produto com o nome '{query}'", "dica": "use search_products"}
        top = [p for p in ranked if score(p) == best]
        if len(top) > 1:
            return {"ambiguo": True, "candidatos": [self._summary(p) for p in top[:6]]}
        return top[0]

    def get_product_details(self, produto: str) -> dict:
        found = self._resolve_product(produto)
        if isinstance(found, dict):
            return found
        product = found
        promo = self.store.active_promotion(product.id)
        details = {
            "id": product.id,
            "nome": product.name,
            "categoria": self.store.category_name(product.category_id),
            "descricao": product.description,
            "especificacoes": product.specs,
            "disponibilidade": product.availability,
            **quote(product.price, promo.discount_percent if promo else None, promo.description if promo else None),
        }
        if product.available and product.stock <= 2:
            details["observacao"] = "últimas unidades em estoque"
        ended = [p for p in self.store.promotions if p.product_id == product.id and not p.active]
        if ended and not promo:
            details["promocoes_encerradas"] = [
                f"{p.description} ({p.discount_percent}%) — encerrada, não vale mais" for p in ended
            ]
        if not product.available:
            details["alternativas_disponiveis"] = [self._summary(p) for p in self._alternatives(product)]
        return details

    def _alternatives(self, product: Product, limit: int = 3) -> list[Product]:
        same = [
            p for p in self.store.products.values()
            if p.category_id == product.category_id and p.available and p.id != product.id
        ]
        same.sort(key=lambda p: abs(self._effective_price(p) - product.price))
        return same[:limit]

    def list_active_promotions(self) -> dict:
        items = []
        for promo in self.store.promotions:
            product = self.store.products.get(promo.product_id)
            if promo.active and product:
                item = self._summary(product)
                item["campanha"] = promo.description
                items.append(item)
        return {"promocoes_ativas": items, "regra": "promoções não acumulam e não somam com o desconto PIX"}

    # ------------------------------------------------------------------ policies
    def get_policy(self, secao: str) -> dict:
        return self.policies.lookup(secao)

    # ------------------------------------------------------------------ orders
    def _verify(self, contato: str) -> Customer | dict:
        contact = str(contato).strip().lower()
        if "@" in contact:
            matches = [c for c in self.store.customers.values() if c.email == contact]
        else:
            digits = re.sub(r"\D", "", contact)
            if len(digits) < 8:
                return {"verificado": False, "mensagem": "Informe o telefone com DDD ou o e-mail usado na compra."}
            matches = []
            for customer in self.store.customers.values():
                phone = re.sub(r"\D", "", customer.phone)
                if phone.endswith(digits) or digits.endswith(phone):
                    matches.append(customer)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return {
                "verificado": False,
                "mensagem": "Esse contato aparece em mais de um cadastro. Para confirmar, informe o telefone com DDD.",
            }
        return {
            "verificado": False,
            "mensagem": "Não encontrei cadastro com esse contato. Confira o telefone com DDD ou o e-mail usado na compra.",
        }

    def _order_for(self, contato: str, numero_pedido) -> tuple[Customer, Order] | dict:
        customer = self._verify(contato)
        if isinstance(customer, dict):
            return customer
        digits = re.sub(r"\D", "", str(numero_pedido))
        order = self.store.orders.get(int(digits)) if digits else None
        if order is None or order.customer_id != customer.id:
            # Same answer whether the order doesn't exist or belongs to someone else.
            return {"verificado": True, "cliente": customer.name.split()[0],
                    "mensagem": f"Não há pedido nº {digits or numero_pedido} vinculado a este cadastro."}
        return customer, order

    def _order_view(self, order: Order) -> dict:
        view = {
            "numero": order.id,
            "data_da_compra": f"{order.order_date:%d/%m/%Y}",
            "status": ORDER_STATUS.get(order.status, order.status),
            "itens": [
                f"{item.quantity}x {self.store.products[item.product_id].name} "
                f"({self.store.category_name(self.store.products[item.product_id].category_id)})"
                if item.product_id in self.store.products else f"{item.quantity}x produto {item.product_id}"
                for item in order.items
            ],
            "total": brl(order.total),
            "pagamento": _payment_pt(order.payment_method),
        }
        if order.tracking_code:
            view["codigo_de_rastreio"] = order.tracking_code
        if order.estimated_delivery:
            view["previsao_de_entrega"] = f"{order.estimated_delivery:%d/%m/%Y}"
        if order.notes:
            view["observacao"] = order.notes
        return view

    def lookup_orders(self, contato: str, numero_pedido: str | int | None = None) -> dict:
        if numero_pedido is not None:
            found = self._order_for(contato, numero_pedido)
            if isinstance(found, dict):
                return found
            customer, order = found
            return {"verificado": True, "cliente": customer.name.split()[0], "pedidos": [self._order_view(order)]}
        customer = self._verify(contato)
        if isinstance(customer, dict):
            return customer
        orders = sorted(
            (o for o in self.store.orders.values() if o.customer_id == customer.id),
            key=lambda o: o.order_date, reverse=True,
        )
        return {
            "verificado": True,
            "cliente": customer.name.split()[0],
            "pedidos": [self._order_view(o) for o in orders] or "nenhum pedido neste cadastro",
        }

    def check_return_options(self, contato: str, numero_pedido: str | int, motivo: str) -> dict:
        found = self._order_for(contato, numero_pedido)
        if isinstance(found, dict):
            return found
        customer, order = found
        today = self.settings.now().date()
        base = {"verificado": True, "cliente": customer.name.split()[0], "pedido": self._order_view(order),
                "data_de_referencia": f"{today:%d/%m/%Y}"}

        if order.status == "cancelled":
            return {**base, "situacao": "Pedido cancelado: não há produto a devolver ou trocar."}
        if order.status in ("pending", "confirmed"):
            return {**base, "situacao": (
                "O pedido ainda não foi enviado. O manual não descreve cancelamento antes do envio: "
                "não prometa nada e encaminhe para a equipe com escalate_to_human.")}
        if order.status == "shipped":
            return {**base, "situacao": (
                "Pedido em transporte. Os prazos de arrependimento e troca contam a partir do recebimento, "
                "então o cliente poderá pedir depois que receber.")}

        received = order.estimated_delivery or order.order_date
        days_received = (today - received).days
        days_bought = (today - order.order_date).days
        base.update({
            "data_de_recebimento_considerada": (
                f"{received:%d/%m/%Y} (previsão de entrega registrada; o sistema não guarda a data real de recebimento)"
            ),
            "dias_desde_o_recebimento": days_received,
            "dias_desde_a_compra": days_bought,
            "itens_nunca_elegiveis_para_troca": (
                "instrumentos personalizados ou com ajuste sob encomenda; itens de liquidação com aviso de 'venda final'; "
                "boquilhas de sopro (seção 4.4). O sistema não registra essas marcações: confirme com o cliente."
            ),
        })
        regret_until = received + timedelta(days=7)
        defect_until = order.order_date + timedelta(days=30)
        legal_until = received + timedelta(days=90)
        motivo = norm(motivo).replace(" ", "_")

        if motivo.startswith("arrependimento"):
            eligible = days_received <= 7
            result = {**base, "motivo": "arrependimento (seção 4.1)", "elegivel": eligible,
                      "prazo": f"até 7 dias corridos após o recebimento — ia até {regret_until:%d/%m/%Y}"}
            if eligible:
                result["condicoes"] = [
                    "produto na embalagem original, sem sinais de uso, com todos os acessórios e manuais",
                    "reembolso na mesma forma de pagamento em até 10 dias úteis",
                    "frete de devolução por conta da loja",
                ]
            else:
                result["outras_opcoes"], result["prazos_ja_encerrados"] = self._open_and_closed(
                    today, defect_until, legal_until)
            return result

        if motivo.startswith("defeito"):
            if days_bought <= 30:
                path = f"troca direta na loja por defeito de fabricação (até {defect_until:%d/%m/%Y}, seção 4.2)"
            elif days_received <= 90:
                path = (f"passou dos 30 dias para troca direta (ia até {defect_until:%d/%m/%Y}), mas está dentro da "
                        f"garantia legal de 90 dias (até {legal_until:%d/%m/%Y}). Pela seção 4.2 a garantia é acionada "
                        "com o fabricante e a loja pode intermediar se o cliente pedir.")
            else:
                path = ("fora da troca direta e da garantia legal; resta a garantia do fabricante (6 meses a 2 anos, "
                        "conforme certificado), e a loja pode intermediar.")
            return {**base, "motivo": "defeito de fabricação (seções 4.2 e 8)", "caminho": path,
                    "nao_coberto": "mau uso, quedas, umidade excessiva, modificações não autorizadas, desgaste natural, "
                                   "danos só estéticos (seções 4.2 e 8.3)",
                    "proximo_passo": "registrar o caso com escalate_to_human para a equipe dar sequência"}

        if motivo.startswith("troca") or motivo.startswith("preferencia"):
            eligible = days_received <= 7
            return {**base, "motivo": "troca por preferência (seção 4.3)", "elegivel": eligible,
                    "prazo": f"até 7 dias — contado a partir do recebimento, ia até {regret_until:%d/%m/%Y}",
                    "condicoes": ["produto em perfeito estado e na embalagem original",
                                  "sujeito à disponibilidade do modelo desejado",
                                  "diferença de valor cobrada ou reembolsada"]}

        return {**base, "erro": "motivo deve ser 'arrependimento', 'defeito' ou 'troca_preferencia'"}

    @staticmethod
    def _open_and_closed(today, defect_until, legal_until) -> tuple[list[str], list[str]]:
        """Split defect/warranty options into still-open and already-closed, so an expired
        deadline is never presented as available."""
        open_, closed = [], []
        exchange = "troca direta na loja por defeito de fabricação (30 dias da compra, seção 4.2)"
        legal = "garantia legal contra defeitos (90 dias do recebimento, seção 8.1)"
        (open_ if today <= defect_until else closed).append(
            f"{exchange}: {'vale até' if today <= defect_until else 'encerrou em'} {defect_until:%d/%m/%Y}")
        (open_ if today <= legal_until else closed).append(
            f"{legal}: {'vale até' if today <= legal_until else 'encerrou em'} {legal_until:%d/%m/%Y}")
        open_.append("garantia do fabricante: de 6 meses a 2 anos, conforme o certificado do produto (seção 8.2)")
        return open_, closed

    # ------------------------------------------------------------------ handoff
    def escalate_to_human(self, motivo: str, resumo: str, contato: str | None = None) -> dict:
        now = self.settings.now()
        protocol = f"EM-{now:%Y%m%d}-{secrets.token_hex(2).upper()}"
        self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        record = {"protocolo": protocol, "criado_em": now.isoformat(), "motivo": motivo, "resumo": resumo,
                  "contato": contato}
        with (self.settings.runtime_dir / "tickets.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        deadline = ("retorno em até 24 horas úteis (seção 7.3)" if norm(motivo).startswith("reclama")
                    else "a equipe retoma no horário de atendimento")
        return {"protocolo": protocol, "encaminhado_para": "equipe de atendimento humano", "prazo": deadline}

    # ------------------------------------------------------------------ schemas
    def schemas(self) -> list[dict]:
        categories = [c.name for c in self.store.categories.values()]

        def tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
            return {"type": "function", "function": {
                "name": name, "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required or []}}}

        contact = {"type": "string", "description": "telefone com DDD ou e-mail do cadastro, informado pelo cliente"}
        return [
            tool("search_products",
                 "Busca instrumentos no catálogo com preço e disponibilidade atuais. Use para perguntas do tipo "
                 "'quais violões até R$1000?', 'tem guitarra?', 'ukulele elétrico'.",
                 {"termo": {"type": "string", "description": "palavras-chave, ex.: 'violão nylon', 'Yamaha', '7 cordas'"},
                  "categoria": {"type": "string", "enum": categories},
                  "preco_max": {"type": "number", "description": "preço máximo em reais"},
                  "preco_min": {"type": "number", "description": "preço mínimo em reais"},
                  "incluir_indisponiveis": {"type": "boolean", "description": "true para incluir esgotados/descontinuados"}}),
            tool("get_product_details",
                 "Detalhes de UM produto: preço, promoção ativa, preço no PIX, parcelamento, estoque, especificações "
                 "e alternativas se estiver indisponível. Use para 'quanto custa o X?', 'tem o X?'.",
                 {"produto": {"type": "string", "description": "nome/modelo (ex.: 'Takamine GD20') ou id"}},
                 ["produto"]),
            tool("list_active_promotions", "Lista as promoções ativas agora.", {}),
            tool("get_policy",
                 "Consulta o manual de políticas da loja: endereço e contato, horário, pagamento e parcelamento, "
                 "trocas e devoluções, frete, promoções, garantia, privacidade.",
                 {"secao": {"type": "string", "enum": list(SECTION_KEYS.values())}}, ["secao"]),
            tool("lookup_orders",
                 "Status, itens, rastreio e previsão de entrega dos pedidos do cliente. Exige o telefone ou e-mail "
                 "do cadastro (proteção de dados).",
                 {"contato": contact, "numero_pedido": {"type": "string", "description": "opcional"}}, ["contato"]),
            tool("check_return_options",
                 "Calcula se um pedido pode ser devolvido ou trocado, com prazos, conforme a política.",
                 {"contato": contact, "numero_pedido": {"type": "string"},
                  "motivo": {"type": "string", "enum": ["arrependimento", "defeito", "troca_preferencia"]}},
                 ["contato", "numero_pedido", "motivo"]),
            tool("escalate_to_human",
                 "Registra o caso para a equipe humana: reclamações, frete para fora de Campo Grande, cancelamentos, "
                 "defeitos, ou qualquer coisa que você não consiga resolver. Retorna um protocolo.",
                 {"motivo": {"type": "string", "enum": ["reclamacao", "defeito", "frete", "cancelamento", "outro"]},
                  "resumo": {"type": "string", "description": "resumo do caso em uma ou duas frases"},
                  "contato": contact},
                 ["motivo", "resumo"]),
        ]
