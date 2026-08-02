-- Migration 013: the subscriber's language.
--
-- The website has spoken eight languages since 2026-08-01. The emails spoke one. A Czech
-- subscriber read a Czech signup form, ticked a Czech consent box, and then received English
-- mail every morning — and every link in it pointed at the English pages, so the site they
-- landed back on was English too.
--
-- The fix needs one stored fact: which language this person signed up in. It cannot be derived
-- at send time, because the digest runs from a timer with no browser and no request headers.
--
--   profiles.language   an entry from `service.i18n.LOCALES`. Defaults to 'en', which is what
--                       every existing row gets — the correct answer for the subscribers who
--                       signed up before the site had any other language, and a safe one for
--                       anybody else.
--
-- Deliberately NOT a foreign key or an enum: the set of languages lives in Python and
-- TypeScript, and a Postgres enum would make adding a language a migration instead of a
-- catalogue file. `i18n.clean_locale` maps anything unrecognised back to 'en' on read, so a
-- stale value degrades to English rather than failing a send. The CHECK below is only a
-- backstop against obviously-wrong writes (an email address, a whole Accept-Language header),
-- not a vocabulary.
--
-- Idempotent, like every migration here: safe to re-run.

alter table profiles
    add column if not exists language text not null default 'en';

alter table profiles
    drop constraint if exists profiles_language_shape;

alter table profiles
    add constraint profiles_language_shape
    check (language ~ '^[a-z]{2}$');

comment on column profiles.language is
    'UI/email language the subscriber signed up in (service.i18n.LOCALES); en for pre-i18n rows.';
