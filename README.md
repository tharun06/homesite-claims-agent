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