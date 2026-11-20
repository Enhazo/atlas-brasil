# Atlas Brasil 🗺️

Projeto pessoal de portfólio, independente das matérias escolares: um explorador interativo da divisão municipal brasileira. Pesquise municípios, filtre por estado/região, compare até quatro lugares e explore a estrutura da base oficial do IBGE.

## O que demonstra

- Coleta e normalização de JSON de uma API pública oficial.
- Persistência relacional com SQLAlchemy e cache local com atualização automática de 24 horas.
- API paginada, filtros, busca sem distinção de caixa e comparação por identificadores.
- Interface responsiva; dados seguem úteis após a primeira sincronização mesmo se a API estiver temporariamente fora do ar.
- Testes sem rede externa com serviço simulado.

## Executar

Requer Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn main:app --reload
```

Abra <http://localhost:8000>. Na primeira visita, o app sincroniza os municípios do [IBGE](https://servicodados.ibge.gov.br/api/docs/localidades); sem rede, será necessário tentar novamente. Depois os dados ficam no `atlas.db` local. Documentação da API própria: <http://localhost:8000/docs>.

## PostgreSQL em Docker Compose

```bash
POSTGRES_PASSWORD='senha-local-forte' docker compose up --build
```

Abra <http://localhost:8003>. Docker Compose não foi executado no ambiente de autoria.

## Testes

```bash
pytest -q
```

Testes usam banco temporário e conjunto sintético de três localidades; não fazem chamadas de rede. Instalação de runtime: `python -m pip install -r requirements.txt`.

## Endpoints

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/states` | Estados únicos e regiões |
| GET | `/api/municipalities?q=&state=&region=&limit=&offset=` | Consulta filtrada e paginada |
| GET | `/api/municipalities/compare?ids=...&ids=...` | Compara dois a quatro municípios por código IBGE |
| POST | `/api/sync` | Atualiza a base a partir do IBGE |

## Fonte e limitações

O projeto é uma demonstração local. Não exponha a instância sem autenticação, limitação de chamadas e proteção da rota de sincronização, que consulta e atualiza toda a base local. A comparação exibe a região imediata e intermediária quando constam nos dados do IBGE; o Atlas não inclui população ou outros indicadores.

Fonte: [API de Localidades do IBGE](https://servicodados.ibge.gov.br/api/docs/localidades). O projeto exibe nomes, regiões, estados e códigos oficiais; **não** contém dados demográficos, população, indicadores socioeconômicos, coordenadas ou mapas. Resultados dependem da disponibilidade e do formato da fonte pública. Os dados são armazenados localmente e a sincronização automática ocorre após 24 horas.

## Licença

MIT para o código deste projeto. Dados e marcas pertencem às fontes e instituições correspondentes; consulte os termos aplicáveis do IBGE.
