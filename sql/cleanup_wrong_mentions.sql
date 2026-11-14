-- sql/cleanup_wrong_mentions.sql
-- Optional one-time cleanup after deploying the updated bot.
-- This script deliberately does NOT filter group names by a simple regex,
-- because the official R3 list contains valid names with spaces, Khmer text,
-- parentheses, Commando, and Specialist labels.

BEGIN;

-- 1) Private Telegram chats use positive IDs and must never be alert groups.
DELETE FROM public.telegram_groups
WHERE group_id > 0;

DELETE FROM public.sa_users
WHERE group_id > 0;

-- 2) Remove unusable rows that have no group identity.
DELETE FROM public.sa_users
WHERE group_name IS NULL
   OR BTRIM(group_name) = '';

-- 3) Normalize usernames only. Keep official group title text unchanged so it
-- continues matching app/data/sa_members.py.
UPDATE public.sa_users
SET username = NULLIF(LOWER(BTRIM(REPLACE(COALESCE(username, ''), '@', ''))), '');

-- 4) Remove duplicate rows only when the username is non-empty and identical
-- inside the same Telegram group ID. Blank usernames are NOT deduplicated by
-- this SQL because many real official users have no username.
DELETE FROM public.sa_users a
USING public.sa_users b
WHERE a."Id" > b."Id"
  AND a.group_id = b.group_id
  AND COALESCE(a.username, '') <> ''
  AND LOWER(BTRIM(a.username)) = LOWER(BTRIM(b.username));

-- 5) Remove incomplete non-list rows. Official list rows are synchronized by
-- /sync_sa_list and by every scheduled alert run.
DELETE FROM public.sa_users
WHERE COALESCE(is_from_list, 0) = 0
  AND (region IS NULL OR dealer IS NULL OR position IS NULL);

COMMIT;

-- After running this file, restart the bot and execute /sync_sa_list in any
-- group you want to refresh immediately. Existing active groups also sync
-- automatically before their next scheduled alert.
