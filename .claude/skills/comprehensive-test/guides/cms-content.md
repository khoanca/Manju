# Test: CMS / Content

## WORKFLOW
- Map: Draft→Review→Approved→Scheduled→Published
- Push entry through every transition; verify visibility at each
- Every step has assigned user/team (audit for "stuck" content)
- Role-based per transition: editors submit, reviewers approve, publishers schedule
- Rejected→correct prior state+reason visible
- Required approvals+translation coverage via pre-publish guards

## RICH TEXT EDITOR
**Input:** ProseMirror/TipTap/Slate: `document.execCommand('insertText',false,'text')` — triggers transaction. Avoid innerHTML/dispatchEvent/keyboard.type()

- All formatting: bold/italic/underline/headings/lists/blockquotes/code/links/images
- Paste from Word(artifacts)/web(HTML)/plain text — sanitize/preserve as designed
- Collab editing: simultaneous edits merge without loss
- Undo/redo integrity after complex ops
- 10K+ words: no typing/scrolling lag

## MEDIA UPLOAD
- All types: PNG/JPG/WebP/AVIF/SVG/GIF/video/audio/PDF/DOCX
- GIF animation preserved (unless configured to flatten)
- Transform fidelity: resize/crop/format conversion
- Size limits: at boundary/over/zero-byte → clear errors
- Concurrent+large(100MB+) with progress
- Responsive display cross-device

## SEO
- Titles+meta descriptions update after CMS changes
- Sitemap: updates on publish/unpublish, correct URLs, excludes draft/private
- Structured data (JSON-LD) validated (Rich Results Test)
- Preview retains all SEO tags
- Canonical URLs, OG tags, Twitter Cards per content type
- robots.txt+noindex for draft/staging

## VERSIONING
- Version on every save, history via UI+API
- Rollback to any previous version (not just prior)
- Content AND metadata revert correctly
- Rollback via API, not just UI
- Time-travel preview any historical version
- Rollback doesn't break media/content refs/internal links

## i18n
- Encoding E2E: form→DB→API→render
- Pseudo-localization in CI: block on missing translations/broken placeholders/layout issues
- Locale formatting: dates/numbers/currencies/calendars/time/phone/postal
- Fallback: missing fr-CA→fr→default
- Long strings (German +30%) + RTL (Arabic/Hebrew) — no truncation/overflow
- Translation coverage audited before publish

## SANITIZATION
- DOMPurify client + server-side both
- CSP headers limiting sources
- Test payloads: `<script>alert(1)</script>`, `<img onerror=alert(1)>`, SVG, mutation XSS
- Editor output sanitized before storage AND rendering
- All inputs validated: content fields/URLs/uploads/search/forms
- JSON Schema for API inputs

## SCHEDULED PUBLISHING
- Publishes at exact time, respecting timezone
- Edge: midnight DST transitions, UTC vs local, different user timezones
- Scheduled content locked; "unschedule→draft" flow
- CRON every minute, reliable
- Visible in preview, NOT public until publication
- Scheduled unpublishing same rigor
- Conflict: 2 users same slot, or depends on unpublished content

## PREVIEW
- Draft mode fetches draft not published
- Matches final: components/layout/styling/media
- Content Source Maps confirm field→UI mapping
- Across states: draft/scheduled/multi-locale
- No leak to unauth users or crawlers
- Performance not significantly slower than published
