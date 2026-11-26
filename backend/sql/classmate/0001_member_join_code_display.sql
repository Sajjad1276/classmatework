-- Keep the raw shared code available to the private admin panel while
-- member_join_code_hash remains the authoritative value for /join validation.
ALTER TABLE public.classes
    ADD COLUMN IF NOT EXISTS member_join_code VARCHAR(64);

COMMENT ON COLUMN public.classes.member_join_code IS
    'Current shared student join code, shown only in the private admin panel.';
***
