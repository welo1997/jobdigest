-- Migration 018: restore, chip-faithfully, what the `other_tech_function` split took from the
-- subscribers holding it.
--
-- On 2026-08-09 the taxonomy stopped being tech-only and `other_tech_function` — a catch-all
-- that had held ~15 000 postings across six professions (sales, operations, marketing, finance,
-- HR, legal) — was narrowed to a small residual. That is a strict improvement for retrieval,
-- but it is a data migration, not just a classifier edit: `role_categories` is a subscriber's
-- stored preference, and two of the three active profiles hold `other_tech_function`. Deploying
-- the classifier change without this would silently remove most of their candidate pool — no
-- error, no failed timer, just a digest that reads as a quiet week (measured: −86.2% pool for
-- `0b9836b5`, −42.3% for `9cd636a1`). That is the exact failure the categorisation plan is
-- written against, so it is fixed here before the batch ships.
--
-- **Chip-faithful, not grant-all.** Before today, the Marketing chip and the Finance chip *both*
-- stored `other_tech_function` (`web/lib/options.ts`), and there was no independent
-- "other tech function" chip — so the stored value is a proxy for exactly one thing: the
-- subscriber ticked Marketing and/or Finance. We cannot tell which (both collapsed to the same
-- value), so we grant both successors — `marketing` and `finance_accounting` — and nothing else.
-- Granting all seven successors, as an earlier draft proposed, would hand a junior content
-- creator legal, HR and operations postings they never asked for.
--
-- **Additive, and `other_tech_function` is kept.** The standing rule is that nothing a
-- subscriber currently matches on may silently vanish; `other_tech_function` remains a valid
-- residual category with its own (now small) inventory, so removing it would be a silent
-- subtraction. Over-delivery of the residual bucket is visible and adjustable in /preferences;
-- under-delivery is invisible. Appending is the safe direction.
--
-- **Idempotent.** Each successor is appended only where it is not already present, so re-running
-- is a no-op. Scoped to profiles that actually hold `other_tech_function`.
--
-- No new data category, processor, or retention — a new *value* in an existing column is not a
-- new field (security rule 4), so no privacy-policy change is triggered.
--
-- **Apply on the box before deploying the taxonomy batch.** Migrations here are manual; no
-- deploy step applies them.

update profiles
set role_categories = role_categories
      || array(select c
               from unnest(array['marketing', 'finance_accounting']) as c
               where c <> all(role_categories))
where 'other_tech_function' = any(role_categories);
