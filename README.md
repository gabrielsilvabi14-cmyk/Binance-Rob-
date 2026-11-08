# Binance Scalper 1.0

Simulador histórico em Python para Binance Spot. Usa dados públicos e um saldo fictício de **R$ 500**, convertido para USDT pela cotação informada no início da simulação.

Pares fixos em ordem de prioridade quando houver sinais simultâneos: **BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT e XRPUSDT**.

## Como executar

Com Python 3.11 ou superior e acesso a `https://data-api.binance.vision`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m binance_scalper --inicio 2026-11-03 --fim 2026-11-05 --brl-por-usdt 5 > relatorio.json
python -m unittest discover -s tests -v
```

No PowerShell, ative o ambiente com `.\.venv\Scripts\Activate.ps1`. Se o Python não tiver dados de fuso horário no sistema, instale `tzdata` com `python -m pip install tzdata`.

As datas usam o fuso de São Paulo. O início é inclusivo e o fim é exclusivo, à meia-noite. A janela aceita de 1 a 31 dias fechados. No exemplo, a taxa ilustrativa de **R$ 5 por USDT** equivale a **100 USDT fictícios**. O teste usa uma taxa fixa de conversão, inclusive para exibir o resultado em reais.

O relatório JSON inclui operações, taxas, derrapagem de preço (*slippage*), saldo final e queda máxima da carteira, medida pelos valores estimados ao fechamento de cada candle de 5 minutos. Os horários das operações são registrados em milissegundos Unix, e os valores decimais são representados como texto para manter a precisão.

## Estratégia e execução simulada

- **Sinal.** EMA 9 acima da EMA 21 nos candles já fechados de 5 e 15 minutos. No de 5 minutos, RSI(14) entre 50 e 68 e volume acima de 1,2 vez a média dos 20 candles anteriores.
- **Entrada.** Compra na abertura do candle seguinte ao sinal, com derrapagem estimada. Os primeiros 100 candles de cada intervalo aquecem os indicadores. Só é aberta uma posição por vez, sem alavancagem.
- **Saída.** Stop a 1,5 ATR(14) da entrada e alvo a 1,8 vez a distância ao stop. Se ambos os níveis forem atingidos durante o candle e a sequência for desconhecida, o stop vem primeiro. Se a abertura já estiver além de um nível, vale o preço da abertura com a derrapagem. A posição fecha em até 60 minutos, ao fim do dia ou no fim do período, o que vier primeiro.
- **Risco.** Até 0,5% do saldo disponível por operação, considerando os custos previstos até o stop. O orçamento diário de perda é 2% do saldo no início do dia. O resultado líquido realizado reduz ou recompõe esse orçamento. A quantidade também respeita o saldo, os incrementos de lote e os limites de valor consultados na Binance. Se o lote mínimo exceder o orçamento, o simulador pula a entrada.
- **Custos.** A simulação assume taxa de 0,10% e derrapagem de 0,05% por lado. São parâmetros do modelo. Um salto de preço pode fazer a perda simulada ultrapassar o orçamento.

## Estrutura

| Arquivo | Responsabilidade |
| --- | --- |
| `binance_scalper/data.py` | Consulta pública, paginação, validação de candles e regras Spot |
| `binance_scalper/strategy.py` | Indicadores e alinhamento dos candles de 5 e 15 minutos |
| `binance_scalper/risk.py` | Orçamento e tamanho das posições simuladas |
| `binance_scalper/simulation.py` | Execução simulada, custos, saldo e relatório |
| `binance_scalper/__main__.py` | Comando de backtest |
| `tests/test_scalper.py` | Testes offline de dados, sinais, risco e execução |

A simulação aplica as regras Spot disponíveis no momento da consulta aos dados históricos. O modelo usa candles de 5 minutos para estimar execuções e pode divergir das condições de liquidez dentro do candle. A integração com a Binance usa exclusivamente consultas públicas, sem credenciais.
