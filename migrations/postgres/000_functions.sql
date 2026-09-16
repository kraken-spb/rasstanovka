-- Explicit equivalents used by the retained staffing queries.
CREATE FUNCTION json_valid(value TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    PERFORM value::json;
    RETURN TRUE;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN FALSE;
END;
$$;

CREATE FUNCTION julianday(value TEXT) RETURNS DOUBLE PRECISION
LANGUAGE plpgsql STABLE STRICT AS $$
BEGIN
    RETURN EXTRACT(EPOCH FROM value::timestamptz) / 86400.0 + 2440587.5;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
    RETURN NULL;
END;
$$;
