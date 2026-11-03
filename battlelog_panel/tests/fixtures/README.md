# Test fixtures (SYNTHETIC)

These files are **synthetic**, written for the automated tests. The field names
follow the Battlelog BF4 JSON shape as known *before* this project (marked
`PRIOR_KNOWLEDGE_UNVERIFIED` in the docs). The values are invented and must
never be shown as real account data. The panel never loads these files at runtime.

To check the parser against real responses, run `python -m blpanel probe ...`
from a network that can reach battlelog.battlefield.com, or import a HAR file
saved from your own browser (see the main README).
