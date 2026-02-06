import uuid

from app.models.rbac import Permission, PersonRole, Role, RolePermission


class TestRolesAPI:
    """Tests for the /rbac/roles endpoints."""

    def test_create_role(self, client, auth_headers):
        """Test creating a new role."""
        payload = {
            "name": f"test_role_{uuid.uuid4().hex[:8]}",
            "description": "Test role description",
        }
        response = client.post("/rbac/roles", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == payload["name"]
        assert data["description"] == payload["description"]
        assert "id" in data

    def test_create_role_unauthorized(self, client):
        """Test creating a role without auth."""
        payload = {"name": "unauthorized_role", "description": "Test"}
        response = client.post("/rbac/roles", json=payload)
        assert response.status_code == 401

    def test_get_role(self, client, auth_headers, role):
        """Test getting a role by ID."""
        response = client.get(f"/rbac/roles/{role.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(role.id)
        assert data["name"] == role.name

    def test_get_role_not_found(self, client, auth_headers):
        """Test getting a non-existent role."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/rbac/roles/{fake_id}", headers=auth_headers)
        assert response.status_code == 404

    def test_list_roles(self, client, auth_headers, role):
        """Test listing roles."""
        response = client.get("/rbac/roles", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_list_roles_with_pagination(self, client, auth_headers, db_session):
        """Test listing roles with pagination."""
        # Create multiple roles
        for i in range(5):
            r = Role(
                name=f"paginated_role_{i}_{uuid.uuid4().hex[:8]}",
                description=f"Role {i}",
            )
            db_session.add(r)
        db_session.commit()

        response = client.get("/rbac/roles?limit=2&offset=0", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 2

    def test_list_roles_with_ordering(self, client, auth_headers):
        """Test listing roles with custom ordering."""
        response = client.get(
            "/rbac/roles?order_by=name&order_dir=desc", headers=auth_headers
        )
        assert response.status_code == 200

    def test_update_role(self, client, auth_headers, role):
        """Test updating a role."""
        payload = {"description": "Updated description"}
        response = client.patch(
            f"/rbac/roles/{role.id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"

    def test_update_role_not_found(self, client, auth_headers):
        """Test updating a non-existent role."""
        fake_id = str(uuid.uuid4())
        payload = {"description": "Updated"}
        response = client.patch(
            f"/rbac/roles/{fake_id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_role(self, client, auth_headers, db_session):
        """Test deleting a role."""
        role = Role(name=f"to_delete_{uuid.uuid4().hex[:8]}", description="To delete")
        db_session.add(role)
        db_session.commit()
        db_session.refresh(role)

        response = client.delete(f"/rbac/roles/{role.id}", headers=auth_headers)
        assert response.status_code == 204

    def test_delete_role_not_found(self, client, auth_headers):
        """Test deleting a non-existent role."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/rbac/roles/{fake_id}", headers=auth_headers)
        assert response.status_code == 404


class TestPermissionsAPI:
    """Tests for the /rbac/permissions endpoints."""

    def test_create_permission(self, client, auth_headers):
        """Test creating a new permission."""
        payload = {
            "key": f"test:perm:{uuid.uuid4().hex[:8]}",
            "description": "Test permission description",
        }
        response = client.post("/rbac/permissions", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["key"] == payload["key"]
        assert "id" in data

    def test_create_permission_unauthorized(self, client):
        """Test creating a permission without auth."""
        payload = {"key": "unauthorized:perm", "description": "Test"}
        response = client.post("/rbac/permissions", json=payload)
        assert response.status_code == 401

    def test_get_permission(self, client, auth_headers, permission):
        """Test getting a permission by ID."""
        response = client.get(
            f"/rbac/permissions/{permission.id}", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(permission.id)
        assert data["key"] == permission.key

    def test_get_permission_not_found(self, client, auth_headers):
        """Test getting a non-existent permission."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/rbac/permissions/{fake_id}", headers=auth_headers)
        assert response.status_code == 404

    def test_list_permissions(self, client, auth_headers, permission):
        """Test listing permissions."""
        response = client.get("/rbac/permissions", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_permissions_with_pagination(self, client, auth_headers, db_session):
        """Test listing permissions with pagination."""
        for i in range(5):
            p = Permission(
                key=f"paginated:perm:{i}:{uuid.uuid4().hex[:8]}",
                description=f"Perm {i}",
            )
            db_session.add(p)
        db_session.commit()

        response = client.get(
            "/rbac/permissions?limit=2&offset=0", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 2

    def test_update_permission(self, client, auth_headers, permission):
        """Test updating a permission."""
        payload = {"description": "Updated permission description"}
        response = client.patch(
            f"/rbac/permissions/{permission.id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated permission description"

    def test_delete_permission(self, client, auth_headers, db_session):
        """Test deleting a permission."""
        perm = Permission(
            key=f"to:delete:{uuid.uuid4().hex[:8]}",
            description="To delete",
        )
        db_session.add(perm)
        db_session.commit()
        db_session.refresh(perm)

        response = client.delete(f"/rbac/permissions/{perm.id}", headers=auth_headers)
        assert response.status_code == 204


class TestRolePermissionsAPI:
    """Tests for the /rbac/role-permissions endpoints."""

    def test_create_role_permission(self, client, auth_headers, role, permission):
        """Test creating a role-permission link."""
        payload = {
            "role_id": str(role.id),
            "permission_id": str(permission.id),
        }
        response = client.post(
            "/rbac/role-permissions", json=payload, headers=auth_headers
        )
        assert response.status_code == 201
        data = response.json()
        assert data["role_id"] == str(role.id)
        assert data["permission_id"] == str(permission.id)

    def test_get_role_permission(
        self, client, auth_headers, db_session, role, permission
    ):
        """Test getting a role-permission link."""
        link = RolePermission(role_id=role.id, permission_id=permission.id)
        db_session.add(link)
        db_session.commit()
        db_session.refresh(link)

        response = client.get(f"/rbac/role-permissions/{link.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(link.id)

    def test_list_role_permissions(self, client, auth_headers):
        """Test listing role-permission links."""
        response = client.get("/rbac/role-permissions", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_role_permissions_filtered(
        self, client, auth_headers, db_session, role, permission
    ):
        """Test listing role-permissions with filter."""
        link = RolePermission(role_id=role.id, permission_id=permission.id)
        db_session.add(link)
        db_session.commit()

        response = client.get(
            f"/rbac/role-permissions?role_id={role.id}", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1

    def test_delete_role_permission(
        self, client, auth_headers, db_session, role, permission
    ):
        """Test deleting a role-permission link."""
        link = RolePermission(role_id=role.id, permission_id=permission.id)
        db_session.add(link)
        db_session.commit()
        db_session.refresh(link)

        response = client.delete(
            f"/rbac/role-permissions/{link.id}", headers=auth_headers
        )
        assert response.status_code == 204


class TestPersonRolesAPI:
    """Tests for the /rbac/person-roles endpoints."""

    def test_create_person_role(self, client, auth_headers, person, role):
        """Test creating a person-role link."""
        payload = {
            "person_id": str(person.id),
            "role_id": str(role.id),
        }
        response = client.post("/rbac/person-roles", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["person_id"] == str(person.id)
        assert data["role_id"] == str(role.id)

    def test_get_person_role(self, client, auth_headers, db_session, person, role):
        """Test getting a person-role link."""
        link = PersonRole(person_id=person.id, role_id=role.id)
        db_session.add(link)
        db_session.commit()
        db_session.refresh(link)

        response = client.get(f"/rbac/person-roles/{link.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(link.id)

    def test_list_person_roles(self, client, auth_headers):
        """Test listing person-role links."""
        response = client.get("/rbac/person-roles", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_person_roles_filtered_by_person(
        self, client, auth_headers, db_session, person, role
    ):
        """Test listing person-roles filtered by person."""
        link = PersonRole(person_id=person.id, role_id=role.id)
        db_session.add(link)
        db_session.commit()

        response = client.get(
            f"/rbac/person-roles?person_id={person.id}", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1

    def test_delete_person_role(self, client, auth_headers, db_session, person, role):
        """Test deleting a person-role link."""
        link = PersonRole(person_id=person.id, role_id=role.id)
        db_session.add(link)
        db_session.commit()
        db_session.refresh(link)

        response = client.delete(f"/rbac/person-roles/{link.id}", headers=auth_headers)
        assert response.status_code == 204


class TestRBACAPIV1:
    """Tests for the /api/v1/rbac endpoints."""

    def test_create_role_v1(self, client, auth_headers):
        """Test creating a role via v1 API."""
        payload = {
            "name": f"v1_role_{uuid.uuid4().hex[:8]}",
            "description": "V1 role",
        }
        response = client.post("/api/v1/rbac/roles", json=payload, headers=auth_headers)
        assert response.status_code == 201

    def test_list_roles_v1(self, client, auth_headers):
        """Test listing roles via v1 API."""
        response = client.get("/api/v1/rbac/roles", headers=auth_headers)
        assert response.status_code == 200

    def test_list_permissions_v1(self, client, auth_headers):
        """Test listing permissions via v1 API."""
        response = client.get("/api/v1/rbac/permissions", headers=auth_headers)
        assert response.status_code == 200
