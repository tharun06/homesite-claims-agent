# HomeSite Claims Verification Agent

A LangGraph-based insurance claims verification pipeline.

## What it does
Takes a video/photo claim submission and decides:
- PASS (auto-approve)
- FAIL (deny)
- FLAG_FRAUD (investigate)
- NEED_MORE_INFO (ask for more evidence)

## Install

pip install -r requirements.txt

## Run with mocks (no Azure needed)

python main.py

## Set up Azure (to use real search)
1. Create Azure AI Search (Free tier) in portal.azure.com
2. Create Azure OpenAI and deploy gpt-4o + text-embedding-3-large
3. Create Azure Blob Storage
4. Fill in real values in .env
5. Run the Azure setup: python azure_setup/setup_all.py
6. Run main.py — now uses real Azure AI Search

## File structure
- state.py        the shared claim data folder
- workflow.py     the LangGraph agent (nodes + edges)
- nodes/          the 6 pipeline steps
- mocks/          fake services (video, vision, fraud)
- azure/          real Azure API calls
- azure_setup/    scripts to create Azure resources

## Windows setup (fresh clone)

Requires **Python 3.11** and **Node.js 18+**. Commands below are for PowerShell.

```powershell
git clone https://github.com/tharun06/homesite-claims-agent.git
cd homesite-claims-agent
```

### 1. Claims agent (Python)

```powershell
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env          # then paste your real Azure keys into .env
```

Run it:

```powershell
python main.py                  # pipeline with mocks (no Azure needed)
streamlit run app.py            # Streamlit UI (expects an API on :8000)
```

### 2. Adjuster dashboard

**Backend** (terminal 1) — FastAPI on port 8100:

```powershell
cd adjuster-dashboard\backend
pip install -r requirements.txt
python -m app.seed                              # build the SQLite database (once)
python -m uvicorn app.main:api --reload --port 8100
```

API docs: http://localhost:8100/docs

**Frontend** (terminal 2) — Vite on port 5173:

```powershell
cd adjuster-dashboard\frontend
npm install
npm run dev
```

Open http://localhost:5173 and pick any user to sign in.

> Note: `.env`, `venv/`, `node_modules/`, and the runtime `*.db` files are
> gitignored — they're recreated by the steps above, not cloned.