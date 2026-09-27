-- function audit_row_change()
CREATE OR REPLACE FUNCTION public.audit_row_change()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
  _oid text; _cols jsonb; _diff text[];
  churn text[] := ARRAY['spend','updated_at','last_active','budget_reset_at','expires','tpm_limit','rpm_limit','max_parallel_requests'];
BEGIN
  IF TG_OP = 'DELETE' THEN
    _oid := COALESCE(to_jsonb(OLD)->>'request_id', to_jsonb(OLD)->>'user_id', to_jsonb(OLD)->>'team_id', to_jsonb(OLD)->>'model_id', to_jsonb(OLD)->>'token', to_jsonb(OLD)->>'id');
    INSERT INTO audit_custom(table_name, action, object_id, payload)
    VALUES (TG_TABLE_NAME, 'DELETE', _oid, jsonb_build_object('old', to_jsonb(OLD)));
    RETURN OLD;
  ELSIF TG_OP = 'INSERT' THEN
    _oid := COALESCE(to_jsonb(NEW)->>'request_id', to_jsonb(NEW)->>'user_id', to_jsonb(NEW)->>'team_id', to_jsonb(NEW)->>'model_id', to_jsonb(NEW)->>'token', to_jsonb(NEW)->>'id');
    INSERT INTO audit_custom(table_name, action, object_id, payload)
    VALUES (TG_TABLE_NAME, 'INSERT', _oid, jsonb_build_object('new', to_jsonb(NEW)));
    RETURN NEW;
  ELSE
    SELECT array_agg(k._col) INTO _diff
    FROM (SELECT jsonb_object_keys(to_jsonb(OLD)) AS _col) k
    WHERE to_jsonb(OLD)->k._col IS DISTINCT FROM to_jsonb(NEW)->k._col;
    IF _diff IS NULL OR _diff <@ churn THEN RETURN NEW; END IF;
    SELECT jsonb_object_agg(d._col, jsonb_build_object('old', to_jsonb(OLD)->d._col, 'new', to_jsonb(NEW)->d._col))
    INTO _cols FROM unnest(_diff) AS d(_col);
    _oid := COALESCE(to_jsonb(NEW)->>'request_id', to_jsonb(NEW)->>'user_id', to_jsonb(NEW)->>'team_id', to_jsonb(NEW)->>'model_id', to_jsonb(NEW)->>'token', to_jsonb(NEW)->>'id');
    INSERT INTO audit_custom(table_name, action, object_id, changed_columns, payload)
    VALUES (TG_TABLE_NAME, 'UPDATE', _oid, array_to_string(_diff, ','), _cols);
    RETURN NEW;
  END IF;
END;
$function$

-- triggers
CREATE TRIGGER trg_audit_user AFTER INSERT OR DELETE OR UPDATE ON public."LiteLLM_UserTable" FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit_key AFTER INSERT OR DELETE OR UPDATE ON public."LiteLLM_VerificationToken" FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit_team AFTER INSERT OR DELETE OR UPDATE ON public."LiteLLM_TeamTable" FOR EACH ROW EXECUTE FUNCTION audit_row_change();
CREATE TRIGGER trg_audit_model AFTER INSERT OR DELETE OR UPDATE ON public."LiteLLM_ProxyModelTable" FOR EACH ROW EXECUTE FUNCTION audit_row_change();
