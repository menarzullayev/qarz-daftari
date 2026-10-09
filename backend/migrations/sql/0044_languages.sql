-- Four more languages (the founder's decision 11 of 2026-10-09): Uzbek in Cyrillic script, Tajik,
-- Karakalpak and English beside Uzbek and Russian.
--
-- A language is stored as its BCP 47 tag, the same six the application accepts
-- (qarz.domain.languages.LANGUAGES; backend/tests/db/test_languages_db.py holds the two lists together):
--
--   uz  uz-Cyrl  ru  tg  kaa  en
--
-- Three columns hold one: a person's own choice, a shop's language and the language recorded for a
-- customer. Each had a check written with the column in 0001 and named by PostgreSQL; it is replaced by
-- a named one. Every stored value is 'uz' or 'ru' today, so the new checks hold for every row; they are
-- still validated here, in the same statement that adds them, so that a row that broke one would stop
-- the migration rather than stay.

ALTER TABLE app_user
  DROP CONSTRAINT app_user_lang_check,
  ADD CONSTRAINT app_user_lang_check CHECK (lang IN ('uz', 'uz-Cyrl', 'ru', 'tg', 'kaa', 'en'));

ALTER TABLE shop
  DROP CONSTRAINT shop_lang_check,
  ADD CONSTRAINT shop_lang_check CHECK (lang IN ('uz', 'uz-Cyrl', 'ru', 'tg', 'kaa', 'en'));

ALTER TABLE customer
  DROP CONSTRAINT customer_lang_check,
  ADD CONSTRAINT customer_lang_check CHECK (lang IN ('uz', 'uz-Cyrl', 'ru', 'tg', 'kaa', 'en'));
