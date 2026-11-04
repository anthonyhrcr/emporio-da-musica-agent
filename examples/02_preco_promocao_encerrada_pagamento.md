# Preço, promoção encerrada e formas de pagamento

- Modelo: `qwen3:8b` via `http://localhost:11434/v1`
- Relógio da loja: 25/03/2026 10:30 (Campo Grande)
- Gerado por `scripts/run_examples.py` em 24s. Respostas do modelo sem nenhuma edição.

**Cliente:** Quanto custa o Takamine GD20?

<details><summary>🔧 Ferramentas consultadas: <code>get_product_details</code></summary>

`get_product_details({"produto": "Takamine GD20"})`

```json
{
 "id": 95,
 "nome": "Takamine GD20 Dreadnought Natural",
 "categoria": "Violões",
 "descricao": "Violão dreadnought Takamine com tampo sólido em cedro. Excelente para fingerstyle com timbre quente e definido.",
 "especificacoes": {
  "top": "Cedro Sólido",
  "back_sides": "Sapele",
  "neck": "Mogno",
  "strings": "steel",
  "scale": "650mm",
  "electronics": "no",
  "color": "Natural"
 },
 "disponibilidade": "disponível",
 "preco_tabela": "R$ 2.199,00",
 "promocao_ativa": null,
 "preco_final": "R$ 2.199,00",
 "preco_no_pix": "R$ 2.089,05",
 "regra_pix": "5% de desconto no PIX sobre o preço de tabela",
 "debito_ou_boleto": "R$ 2.199,00",
 "parcelamento_maximo_sem_juros": "12x de R$ 183,25 no cartão de crédito",
 "pode_combinar_formas_de_pagamento": true,
 "promocoes_encerradas": [
  "Oferta Especial (12%) — encerrada, não vale mais"
 ]
}
```

</details>

**Lia:**

> O Takamine GD20 está disponível e custa:
> 
> - **Preço final:** R$ 2.199,00  
> - **PIX:** R$ 2.089,05 (5% de desconto)  
> - **Débito ou boleto:** R$ 2.199,00  
> - **Parcelamento:** 12x de R$ 183,25 no cartão de crédito  
> 
> Se quiser, posso te ajudar a escolher um violão que combine com o seu estilo! 🎸

**Cliente:** Vi que ele tava com 12% de desconto, ainda vale?

**Lia:**

> A oferta especial de 12% estava encerrada, então não vale mais. O preço final continua R$ 2.199,00. Se quiser, posso te mostrar outros modelos com descontos ativos! 🎸

**Cliente:** E no PIX sai quanto? Dá pra parcelar em 12x?

**Lia:**

> - **PIX:** R$ 2.089,05 (5% de desconto sobre o preço de tabela)  
> - **Parcelamento:** Sim, 12x de R$ 183,25 no cartão de crédito  
> 
> Quer que eu te ajude a montar um orçamento? 🎸
