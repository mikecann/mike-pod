# Mike Pod

Mike Pod is a personalised, research-first podcast for Mike. It follows
interesting questions through science, computation, technology, biology,
history, warfare and space, then tests the exciting story against the best
evidence it can find.

The canonical feed is:

`https://podcast.mikecann.app/feed.xml`

Cloudflare R2 is the live publishing origin. Bruce is not required for
generation, RSS publishing or playback.

## How an episode is made

1. A topic comes directly from Mike, or GPT-5.6 Sol, Claude Fable 5 and Grok
   4.6 independently rank the eligible questions in `research_topics.json`.
   Sol chairs the recorded panel decision after seeing all three views.
2. Mike's dated writing, projects, saved items and saved comments identify
   personally relevant research branches.
3. GPT-5.6 Sol plans the research, then Grok 4.6 uses its CLI web tools to find
   current primary, opposing and contextual sources. `deep_dive.py`
   independently fetches and snapshots every selected URL.
4. Sol synthesises a source-linked dossier that includes the history of the
   claim, the newest evidence, and serious corrections or refutations.
5. Claude Fable and Grok independently review the dossier. Both must approve.
6. Sol writes an accessible single-narrator script only after dossier
   approval, assuming curiosity but no prior knowledge of the episode's
   subject. It establishes why the question matters before explaining how the
   underlying mechanism works.
7. Fable and Grok separately critique the draft for factual support,
   calibration, personalisation and accessibility. Sol revises from both sets
   of notes, including a dedicated check that the final third stays as clear as
   the opening. Both peers must independently approve the final script.
8. ElevenLabs narrates the approved script with David.
9. `ffmpeg` normalises the mono MP3 to -19 LUFS and a -1 dB true-peak ceiling.
10. Artwork and audio are uploaded and byte-verified before the RSS feed is
   uploaded last.

StashIt is one interest signal, not an episode queue. The local Second Brain
database is a historical snapshot whose newest record is currently April 2026.
It does not establish private listening history.

## Research

List enduring interests and active prompts:

```bash
python3 deep_dive.py --list-interests
```

Build the Wolfram physics pilot dossier:

```bash
python3 deep_dive.py --interest wolfram-evidence-pilot
```

Research a one-off question:

```bash
python3 deep_dive.py --topic "How close is fault-tolerant quantum computing?"
```

Let the three-model panel select among eligible unpublished prompts or enduring
interests before research begins:

```bash
python3 deep_dive.py --ensemble-select
```

Topic-panel recommendations, disagreements and provenance remain private under
`data/topic_panels/`.

Use `--plan-only` to inspect the personal context and proposed branches before
web discovery. Research packages and source snapshots remain private under
`data/deep_dives/`.

## Episode production

Turn an approved dossier into an audited text package without spending
ElevenLabs credits:

```bash
python3 episode.py \
  --dossier-dir data/deep_dives/<approved-run> \
  --episode-number 2 \
  --episode-slug <ascii-slug> \
  --draft-only
```

After producing the episode artwork, resume the approved package for David
narration:

```bash
python3 episode.py \
  --dossier-dir data/deep_dives/<approved-run> \
  --episode-number 2 \
  --episode-slug <ascii-slug> \
  --episode-artwork assets/artwork/final/<episode-artwork>.jpg \
  --resume
```

To replace a published episode without overwriting its immutable enclosure,
produce a higher revision of the same episode number. Revision 1 keeps the
original filenames; later revisions receive a new GUID and public filename:

```bash
python3 episode.py \
  --dossier-dir data/deep_dives/<approved-run> \
  --episode-number 2 \
  --revision 2 \
  --episode-slug <ascii-slug> \
  --draft-only
```

The feed includes only the highest published revision of each episode. Older
enclosure bytes remain available at their immutable URLs.

Build the RSS bundle:

```bash
python3 feed.py
```

Publish media and artwork first, verify the exact R2 bytes, then publish and
verify the feed:

```bash
python3 publish.py
```

Generated release packages live under `data/releases/`. The public staging
bundle is built under `dist/podcast/`. Both are ignored by Git.

`PRODUCTION.md` is the contract for the weekly Codex automation, including its
quality gates, stop conditions and recovery behaviour.

## Artwork

The 3000 by 3000 show and first-episode art use original generated base
illustrations with deterministic typography added by `artwork.py`:

```bash
/Users/m5-mike/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 artwork.py
```

Final reusable assets are in `assets/artwork/final/`.

## Credentials

The production scripts use existing local credentials and do not copy them into
the repository:

- Codex CLI must be logged in and able to run `gpt-5.6-sol` for topic-panel
  advice and consensus. It is invoked read-only and ephemerally.
- Claude CLI must be logged in. The production path calls its `fable` alias,
  currently Claude Fable 5, with tools and session persistence disabled.
- Grok CLI must be logged in. The production path pins `grok-4.6`. Topic and
  audit calls disable tools, memory and subagents; source discovery enables
  only Grok's web-search and web-fetch tools.
- ElevenLabs is loaded from `ELEVENLABS_API_KEY`,
  `~/.config/elevenlabs_api_key`, or the `mike-pod-elevenlabs` macOS Keychain
  entry.
- Cloudflare publishing uses this Mac's authenticated Wrangler profile.

The dedicated ElevenLabs key is restricted to text-to-speech, voice read and
user read. Its monthly key allowance is 20,000 credits. The automation stops
and reports a shortfall rather than buying a larger plan.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Legacy Bruce files

`research.py`, `deep_research.py`, `generate.py`, `config.py` and
`webhook_runner.py` are copied Bruce-era implementation kept for reference.
They are not the supported production path. In particular, the old webhook
must not be restored because its response exposed an SSH private key.
