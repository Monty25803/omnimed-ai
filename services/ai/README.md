# ai

Clinical language service, written in Python with FastAPI. It is rule-based by default, so it runs without any
external model. An OpenAI-compatible LLM can be switched on, but its output still goes through the same grounding
guardrail.

Gateway prefix: `/api/ai` · Port: `8084`

## Endpoints

| Method and path | Purpose |
|---|---|
| `POST /scribe` | Turn a consultation transcript into a structured note with ICD-10 and RxNorm codes |
| `POST /scribe/audio` | Same, from audio (requires `WHISPER_URL`) |
| `POST /codify` | Code free text to ICD-10, RxNorm and LOINC |
| `POST /guardrail/check` | Accept or reject a proposed note against its sources |
| `POST /differential` | Ranked differential diagnosis from findings, labs and vitals |
| `POST /translate` | Patient-language explanation of results, in English or Hindi |
| `POST /ocr`, `POST /ocr/text`, `GET /ocr/sample` | Read a prescription image or text into structured, coded lines |
| `GET /ontology/search` | Search the bundled vocabulary |

## The grounding guardrail

Every drug, diagnosis, test and strength in a proposed note must be supported by the transcript, the patient's
record or the vocabulary, and strengths must fall inside the drug's known range. The free-text narrative is also
scanned for drug-like words that appear nowhere in the sources. If anything is unsupported the note is rejected, and
the scribe falls back to the rule-based draft.

The clinical service calls this endpoint itself before saving any note, so a client cannot skip it.

## Configuration

| Variable | Effect |
|---|---|
| `LLM_API_KEY` | Turns on LLM drafting (off when empty) |
| `LLM_BASE_URL`, `LLM_MODEL` | Any OpenAI-compatible endpoint; defaults `https://api.openai.com/v1`, `gpt-4o-mini` |
| `WHISPER_URL`, `WHISPER_MODEL` | OpenAI-compatible speech-to-text, for example faster-whisper-server |

## Limits

The bundled vocabulary is small (25 ICD-10 diagnoses, 36 RxNorm drugs, 32 LOINC tests and 52 SNOMED CT concepts) and was written by the same team as
the accuracy tests. Load licensed SNOMED CT, RxNorm and LOINC releases before relying on coding accuracy. See
[docs/IMPROVEMENTS.md](../../docs/IMPROVEMENTS.md).

## Develop

```bash
docker compose -f infra/docker-compose.yml up -d --build ai
```

The 18 unit tests in `tests/test_units.py` run during the image build.
