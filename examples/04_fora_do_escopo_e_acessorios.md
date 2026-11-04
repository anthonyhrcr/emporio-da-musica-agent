# Fora do escopo: acessórios, assunto aleatório e categoria vazia

- Modelo: `qwen3:8b` via `http://localhost:11434/v1`
- Relógio da loja: 25/03/2026 10:30 (Campo Grande)
- Gerado por `scripts/run_examples.py` em 25s. Respostas do modelo sem nenhuma edição.

**Cliente:** Vocês vendem cordas e amplificador?

**Lia:**

> Não, nossa loja vende **apenas instrumentos musicais**, como violões, guitarras, baixos, teclados, baterias e outros. Não trabalhamos com acessórios como cordas, amplificadores ou pedais. Se precisar de ajuda com algum instrumento ou política da loja, é só me dizer! 🎸

**Cliente:** E quem ganhou o jogo do Flamengo ontem?

**Lia:**

> Sinto muito, mas não posso ajudar com assuntos fora da loja, como futebol ou notícias. Posso só te ajudar com instrumentos, pedidos ou políticas da Empório da Música! 🎸

**Cliente:** Beleza. Vocês têm saxofone?

<details><summary>🔧 Ferramentas consultadas: <code>search_products</code></summary>

`search_products({"categoria": "Instrumentos de Sopro (Metais)", "termo": "saxofone", "incluir_indisponiveis": false})`

```json
{
 "criterio_de_preco": "preço final, já com promoção ativa; desconto PIX não incluído",
 "total_encontrado": 0,
 "produtos": [],
 "categorias_sem_produtos": "Nenhum produto à venda em Instrumentos de Sopro (Madeiras), Instrumentos de Sopro (Metais). A loja não tem nenhum instrumento dessa categoria disponível agora: não cite modelos nem tipos de instrumento dessa categoria."
}
```

</details>

**Lia:**

> Poxa, não temos saxofones disponíveis no momento. Mas se quiser, posso te avisar quando tivermos em estoque! 🎺 Ou pode me dizer se está interessado em outro instrumento?
