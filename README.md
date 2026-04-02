# Codex

Automated content bot that continuously writes article drafts about the **Chinet** tableware brand using publicly available web pages.

## What it does

- Polls a configured set of public URLs (`sources.json`)
- Extracts page text and detects unseen updates
- Uses the OpenAI API to draft a fresh article when new content appears
- Saves each article as markdown in `articles/`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=your_key_here
```

Alternative (if you do not want to export an environment variable each time):

```bash
python article_bot.py --api-key your_key_here --run-once
```

Or keep the key in a file:

```bash
printf "your_key_here" > .openai_key
python article_bot.py --api-key-file .openai_key --run-once
```

## Run

Run one cycle:

```bash
python article_bot.py --run-once
```

Run continuously every 2 hours (default):

```bash
python article_bot.py
```

Run continuously with a custom interval:

```bash
python article_bot.py --interval-minutes 30
```

## Configure sources

Edit `sources.json`:

```json
{
  "sources": [
    { "name": "Source name", "url": "https://example.com/page" }
  ]
}
```

## Notes

- This tool only uses publicly reachable pages you configure.
- You are responsible for complying with each website's terms of use and robots policy.
- Output is AI-generated draft content and should be reviewed before publishing.
