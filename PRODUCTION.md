# Mike Pod production policy

The Thursday automation owns production on this Mac. Bruce is not part of the
live publishing path.

## Editorial contract

- Select one question from Mike's active prompts or enduring interests in
  `research_topics.json`. Prefer a prompt whose status is not `published`, then
  rotate enduring interests. Do not repeat a published episode.
- Use StashIt and the local personal-context corpus only as evidence of what Mike
  has read, saved, written or commented on. They are not the episode queue and
  they do not prove private listening history.
- Build a branching research dossier with mechanisms, empirical evidence,
  criticism, an adjacent connection, practical implications and at least one
  disconfirming branch.
- Prefer primary sources, papers and official technical material. Include
  serious opposition and distinguish project-authored claims from independent
  validation.
- A second model must approve the dossier before scripting. A separate audit
  must approve the script before narration.
- Write for one technically experienced, curious listener. Do not invent Mike's
  opinions or tell him what he believes.
- Narrate with David in ElevenLabs, then normalise mono audio to -19 LUFS with a
  -1 dB true-peak ceiling.

## Release contract

- Give every episode an increasing episode number, immutable GUID and unique
  ASCII MP3 filename.
- Produce source-linked show notes and a 3000 by 3000 episode image that belongs
  to the Mike Pod cyan, amber and midnight-navy visual family.
- Build the RSS bundle with `python3 feed.py`.
- Upload artwork and audio first. Verify the exact R2 bytes. Upload `feed.xml`
  last with `python3 publish.py`.
- Verify public `HEAD` responses, exact content lengths, RSS XML parsing, and an
  HTTP 206 byte-range response from the MP3.
- The canonical public feed is `https://podcast.mikecann.app/feed.xml`.
- Keep `podcast.mikecann.blog` attached to the same bucket as a compatibility
  origin while podcast clients migrate to the `.app` feed.

## Stop conditions

Do not publish an episode when:

- either independent editorial gate is not approved;
- the evidence set cannot support the main conclusion;
- source IDs are spoken in the script or factual claims lose their attribution;
- ElevenLabs lacks sufficient credits;
- audio inspection fails;
- an immutable public episode filename already contains different bytes; or
- the Cloudflare origin cannot be verified.

Report the exact failed gate rather than weakening it or buying a larger paid
plan automatically. The user can raise the ElevenLabs plan later if weekly
episodes consistently need more than the current allowance.

## Recovery

Cloudflare R2 is the live origin. Bruce may later receive a mirror of published
release packages, but a Bruce outage must never break generation, RSS, or
playback.
