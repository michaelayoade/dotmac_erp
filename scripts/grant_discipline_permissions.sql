-- Grant discipline permissions without granting hr:access.
-- Target roles:
--   department_manager, finance_manager, inventory_manager, operations_manager

BEGIN;

WITH new_permissions(key, description) AS (
    VALUES
        ('discipline:access', 'Access discipline management'),
        ('discipline:cases:read', 'View disciplinary cases'),
        ('discipline:cases:create', 'Create disciplinary cases'),
        ('discipline:cases:update', 'Update disciplinary case records'),
        ('discipline:workflow:manage', 'Manage disciplinary workflow actions')
)
INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
SELECT gen_random_uuid(), np.key, np.description, TRUE, NOW(), NOW()
FROM new_permissions np
WHERE NOT EXISTS (
    SELECT 1 FROM permissions p WHERE p.key = np.key
);

WITH target_roles(name) AS (
    VALUES
        ('department_manager'),
        ('finance_manager'),
        ('inventory_manager'),
        ('operations_manager')
),
target_permissions(key) AS (
    VALUES
        ('discipline:access'),
        ('discipline:cases:read'),
        ('discipline:cases:create'),
        ('discipline:cases:update'),
        ('discipline:workflow:manage')
)
INSERT INTO role_permissions (id, role_id, permission_id)
SELECT
    gen_random_uuid(),
    r.id,
    p.id
FROM target_roles tr
JOIN roles r ON r.name = tr.name
JOIN target_permissions tp ON TRUE
JOIN permissions p ON p.key = tp.key
WHERE NOT EXISTS (
    SELECT 1
    FROM role_permissions rp
    WHERE rp.role_id = r.id
      AND rp.permission_id = p.id
);

COMMIT;
