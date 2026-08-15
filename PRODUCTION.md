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
- Write for one curious generalist who happens to build software. Assume no
  prior knowledge of the episode's subject. Software experience is useful
  context for an occasional analogy, not permission to pitch the episode at an
  expert or enthusiast level. Do not invent Mike's opinions or tell him what he
  believes.
- Earn the listener's attention before teaching the mechanism. The opening must
  explain, in ordinary language, what problem people are trying to solve, why
  it matters, what could change if they succeed, and what the new result adds.
  Mike should understand why the story is worth hearing before specialist
  vocabulary or implementation detail appears.
- In the first 200 spoken words, answer four questions: what larger goal this
  work serves, what currently blocks that goal, why the result is meaningful
  progress, and what it still does not enable. Do this before an analogy. An
  analogy can explain a mechanism, but it cannot substitute for motivation.
- Prefer three well-explained ideas over a tour of every research
  branch. Introduce one new abstraction at a time, define unavoidable jargon in
  the same sentence, and keep equations and unexplained initialisms out of the
  spoken script.
- Apply a strict "so what?" test to every mechanism, number and caveat. Keep a
  technical detail only when it changes the listener's understanding of the
  significance, the strength of the evidence or the practical limitation. Put
  useful specialist detail in the source-linked show notes instead of the
  spoken script.
- Prefer qualitative scale and consequence over exact figures. Speak a number,
  acronym or implementation name only when the listener needs it to understand
  the conclusion; keep supporting measurements in the show notes.
- Treat two exact measurements as the normal ceiling for a spoken episode and
  one bounded analogy as enough. Organise the story around: the larger goal and
  blocker; what the new evidence means; and the honest limitation. Do not turn
  the limitations section into a catalogue of every caveat in the dossier.
- Use concrete examples and familiar software, game or everyday analogies where
  they genuinely clarify an idea. Say briefly where an analogy stops matching
  the science so it does not become a misleading explanation.
- The script audit must reject an episode that is accurate but unnecessarily
  dense, assumes domain expertise, starts in the weeds before establishing the
  stakes, or explains terms individually while leaving the overall significance
  unclear. It must also reject scripts that stack technical terms without
  returning to a concrete example and a plain-language "why this matters".
- Narrate with David in ElevenLabs, then normalise mono audio to -19 LUFS with a
  -1 dB true-peak ceiling.

## Release contract

- Give every episode an increasing episode number, immutable GUID and unique
  ASCII MP3 filename.
- A corrected cut of a published episode must use a higher revision with a new
  immutable GUID and enclosure filename. Keep the original object available,
  but list only the highest published revision of that episode in the feed.
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
