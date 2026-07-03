# OptiBot Support RAG

## Overview

OptiBot is a mini customer-support assistant for OptiSigns documentation. It scrapes support articles from `support.optisigns.com`, converts cleaned article HTML into Markdown, stores source citations, and syncs only changed content into Gemini File Search for retrieval-augmented answers.

## Architecture

1. Zendesk Help Center API fetches support articles.
2. BeautifulSoup cleans noisy HTML and `markdownify` converts article bodies to Markdown.
3. Markdown files are saved under `data/markdown/` with an `Article URL:` line for citation.
4. Content hashes detect added, updated, and unchanged articles.
5. Added or updated Markdown files are imported into Gemini File Search Store.
6. The assistant queries Gemini with File Search enabled and answers only from uploaded docs.

## Tech Stack

- Python 3.11
- requests, BeautifulSoup, markdownify
- google-genai
- python-dotenv
- Docker
- Railway or Render cron job

## Setup

API keys are not committed. Copy `.env.sample` or `.env.example` to `.env` and configure:

```env
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-2.5-flash
ZENDESK_SUBDOMAIN=support.optisigns.com
ZENDESK_ARTICLE_LIMIT=30
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run Locally

Run the full ingestion pipeline:

```bash
python -m rag_pipeline.main ingest --limit 30
```

Run Gemini File Search sync only:

```bash
python -m rag_pipeline.main upload-vector
```

Ask the assistant:

```bash
python assistant/ask.py "How do I add a YouTube video?"
```

## Docker

Build and run the ingestion job:

```bash
docker compose up --build
```

Docker requires the same environment variables from `.env`. Runtime state is mounted through `data/` and `logs/`.

## Gemini File Search / RAG

Gemini File Search handles chunking, embedding, indexing, and retrieval as a managed RAG service. This project focuses on producing clean, citation-ready Markdown and delta syncing only changed files.

Delta sync uses content hashes: new and updated Markdown files are imported into Gemini File Search, while unchanged files are skipped to reduce indexing cost and quota usage.

Assistant system instruction:

```text
You are OptiBot, the customer-support bot for OptiSigns.com.
• Tone: helpful, factual, concise.
• Only answer using the uploaded docs.
• Max 5 bullet points; else link to the doc.
• Cite up to 3 "Article URL:" lines per reply.
```

## Daily Job

The project is designed to run as a daily cron job on Railway or Render. The scheduled job re-scrapes at least 30 articles, detects deltas, syncs only new or updated Markdown files, and logs counts for added, updated, skipped, imported, and failed files.

## Sample Assistant Result

```text
Question: How do I add a YouTube video?

Answer:
- Open the relevant OptiSigns app or asset workflow for YouTube content.
- Add the YouTube URL and configure playback options as described in the support article.
- Save the asset and assign it to a screen or playlist.

Article URL: <source article URL from uploaded docs>
```
