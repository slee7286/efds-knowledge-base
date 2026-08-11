# ICU corpus assessment

This assessment was made from the current `icu-crawler/data/` archive before
the structured schema was added. It is intentionally a snapshot of the source
corpus, not an authoritative copy of the articles.

## Inventory

- 78 current article metadata records; all IDs and content hashes are unique.
- 77 articles are in `Committee Member Resources`; one legacy item is in
  `Knowledge base`.
- 17 category/folder groupings. The largest are Events & Trips (14), Committee
  Management (9), Summer Admin (8), and Finances, Services, Compliance &
  Regulation, Minibuses, and Funding (5 each).
- 28 attachment/resource references and 143 external-link references occur in
  the article metadata. The archive also contains current Markdown/HTML,
  manifests, SQLite crawl state, and downloaded PDF/DOCX resources.
- Article text is approximately 853k characters. The corpus contains both
  short FAQs and very long guidance articles, including a roughly 70k-character
  high-risk documentation guide.

## Recurring knowledge

The stable operational shapes are obligations, timing rules, multi-step
processes, links/forms/systems, contacts, and article-to-article references.
Recurring subjects are finance and funding, sponsorship, event planning and
risk, room bookings, minibuses/trips, committee administration, SUMS and
eActivities, training, wellbeing, compliance, and external speakers/suppliers.

Common timing forms include absolute dates and date ranges, “at least N working
days in advance”, processing durations, annual/September windows, and
term-based language. These cannot safely share one absolute-date field.

Explicit obligation language is frequent (`must`, `required`, `need to`,
`have to`, and “expected to”), while many statements remain descriptive. A
deterministic extractor can safely identify the explicit language and preserve
the complete evidence sentence; semantic interpretation is still needed to
decide whether a paraphrased statement is a requirement and who it applies to.

## Data-quality observations

- Freshdesk HTML-to-Markdown output contains duplicated sections in several
  articles, so extraction deduplicates by version/evidence fingerprint.
- Links sometimes appear as Markdown links and sometimes as naked URLs beside
  their anchor text.
- Date text mixes years, ranges, ordinal suffixes, seasonal language, and old
  guidance dates; the extractor never invents a year.
- Encoding artefacts such as `Â£` and typographic mojibake occur in the
  archived text and are retained as source evidence rather than silently
  rewritten.
- Three known URLs in the latest crawl manifest failed to download, and some
  links require authentication. These remain source/resource metadata concerns
  owned by the crawler.

## Extraction boundary

Deterministic extraction is used for metadata, folder/category, headings,
numbered steps, explicit obligation sentences, dates, relative periods, URLs,
emails, and resource classification. It also supplies a conservative first
pass for EFDS relevance, topics, and role mappings.

An LLM is justified later for paraphrased policy meaning, ambiguous role
applicability, process normalization across prose, ambiguous timing semantics,
and concise summaries. It is optional and must return proposed, source-linked
records through the same review lifecycle; no credentials are required for the
deterministic pipeline.

Running the deterministic layer read-only over the current archive produced
approximately 329 requirement candidates, 47 timing candidates, 23 process
candidates containing 99 steps, 375 resource candidates, and 28 contact
candidates. These are candidates, not approved policy; duplicated source
sections are fingerprint-deduplicated during persistence.

## Proposed model

`knowledge_articles` remains the one current authoritative ICU row. Relevance
fields live there, while `knowledge_topics`/`knowledge_roles` and their two
association tables support dashboards and SQL filters. Typed derived tables
cover `knowledge_requirements`, `knowledge_timing_rules`,
`knowledge_processes`/`knowledge_process_steps`, `knowledge_resources`, and
`knowledge_contacts`. Process-role/resource and requirement-role associations
remain reusable many-to-many tables.

Each derived row stores the source article ID, source URL, source content hash,
source updated timestamp, evidence text/span, extraction run, method,
confidence, review metadata, fingerprint, and `is_stale`. This avoids copying
the full corpus while ensuring that no operational fact is source-less.
