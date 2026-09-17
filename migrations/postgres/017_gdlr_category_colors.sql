ALTER TABLE gdlr_categories ADD COLUMN color TEXT NOT NULL DEFAULT '#2563EB'
    CHECK(color ~ '^#[0-9A-Fa-f]{6}$');

-- Stable initial palette for the twelve placement categories, editable in the catalog.
UPDATE gdlr_categories SET color=CASE name_key
    WHEN 'арматурщик' THEN '#2563EB'
    WHEN 'бетонщик' THEN '#0D9488'
    WHEN 'монтажник сижбк' THEN '#D97706'
    WHEN 'монтажник тт' THEN '#7C3AED'
    WHEN 'прочие монтажники' THEN '#DB2777'
    WHEN 'прочие основные рабочие' THEN '#0891B2'
    WHEN 'прочие сварщики и газорезчики' THEN '#65A30D'
    WHEN 'сварщик аипам' THEN '#E11D48'
    WHEN 'сварщик мк' THEN '#4F46E5'
    WHEN 'сварщик тт' THEN '#059669'
    WHEN 'электромонтажник' THEN '#C2410C'
    WHEN 'изолировщик' THEN '#9333EA'
    ELSE (ARRAY['#2563EB','#0D9488','#D97706','#7C3AED','#DB2777','#0891B2',
                '#65A30D','#E11D48','#4F46E5','#059669','#C2410C','#9333EA'])[1+((id-1)%12)::integer]
END;
