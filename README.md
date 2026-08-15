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

1. A topic comes from Mike or `research_topics.json`.
2. Mike's dated writing, projects, saved items and saved comments identify
   personally relevant research branches.
3. `deep_dive.py` finds and snapshots current primary, opposing and contextual
   sources.
4. Claude synthesises a source-linked dossier.
5. A different model independently reviews the dossier.
6. Claude writes an accessible single-narrator script only after dossier
   approval, assuming curiosity but no prior knowledge of the episode's
   subject. It establishes why the question matters before explaining how the
   underlying mechanism works.
7. A different model checks factual support, calibration, personalisation and
   whether the episode is pitched at a useful conceptual altitude, with the
   stakes clear before technical ideas, concrete examples and bounded analogies.
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

- OpenRouter is loaded from `OPENROUTER_API_KEY`,
  `~/.config/openrouter_api_key`, or the existing local
  `~/dev/convex/convex-evals/.env`.
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
