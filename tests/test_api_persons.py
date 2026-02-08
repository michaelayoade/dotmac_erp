import uuid

from tests.conftest import DEFAULT_TEST_ORG_ID


class TestPersonsAPI:
    """Tests for the /people API endpoints."""

    def test_create_person(self, client, auth_headers):
        """Test creating a new person."""
        payload = {
            "first_name": "John",
            "last_name": "Doe",
            "email": f"john.doe.{uuid.uuid4().hex[:8]}@example.com",
            "organization_id": str(DEFAULT_TEST_ORG_ID),
        }
        response = client.post("/people", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["first_name"] == "John"
        assert data["last_name"] == "Doe"
        assert "id" in data

    def test_create_person_with_all_fields(self, client, auth_headers):
        """Test creating a person with all optional fields."""
        payload = {
            "first_name": "Jane",
            "last_name": "Smith",
            "email": f"jane.smith.{uuid.uuid4().hex[:8]}@example.com",
            "phone": "+1234567890",
            "display_name": "Jane S.",
            "locale": "en-US",
            "timezone": "America/New_York",
            "organization_id": str(DEFAULT_TEST_ORG_ID),
        }
        response = client.post("/people", json=payload, headers=auth_headers)
        assert response.status_code == 201
        data = response.json()
        assert data["first_name"] == "Jane"
        assert data["phone"] == "+1234567890"
        assert data["locale"] == "en-US"

    def test_create_person_unauthorized(self, client):
        """Test that creating a person requires authentication."""
        payload = {
            "first_name": "Test",
            "last_name": "User",
            "email": "test@example.com",
        }
        response = client.post("/people", json=payload)
        assert response.status_code == 401

    def test_get_person(self, client, auth_headers, person):
        """Test getting a person by ID."""
        response = client.get(f"/people/{person.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(person.id)
        assert data["first_name"] == person.first_name

    def test_get_person_not_found(self, client, auth_headers):
        """Test getting a non-existent person."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/people/{fake_id}", headers=auth_headers)
        assert response.status_code == 404

    def test_list_people(self, client, auth_headers, person):
        """Test listing people."""
        response = client.get("/people", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_list_people_with_pagination(self, client, auth_headers, db_session):
        """Test listing people with pagination."""
        from app.models.person import Person

        # Create multiple people
        for i in range(5):
            p = Person(
                first_name=f"Test{i}",
                last_name="User",
                email=f"test{i}_{uuid.uuid4().hex[:8]}@example.com",
                organization_id=DEFAULT_TEST_ORG_ID,
            )
            db_session.add(p)
        db_session.commit()

        response = client.get("/people?limit=2&offset=0", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 2

    def test_list_people_with_filters(self, client, auth_headers, person):
        """Test listing people with email filter."""
        response = client.get(f"/people?email={person.email}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1

    def test_list_people_with_ordering(self, client, auth_headers):
        """Test listing people with custom ordering."""
        response = client.get(
            "/people?order_by=last_name&order_dir=asc", headers=auth_headers
        )
        assert response.status_code == 200

    def test_update_person(self, client, auth_headers, person):
        """Test updating a person."""
        payload = {"first_name": "Updated"}
        response = client.patch(
            f"/people/{person.id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["first_name"] == "Updated"

    def test_update_person_multiple_fields(self, client, auth_headers, person):
        """Test updating multiple fields of a person."""
        payload = {
            "first_name": "UpdatedFirst",
            "last_name": "UpdatedLast",
            "phone": "+9876543210",
        }
        response = client.patch(
            f"/people/{person.id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["first_name"] == "UpdatedFirst"
        assert data["last_name"] == "UpdatedLast"
        assert data["phone"] == "+9876543210"

    def test_update_person_not_found(self, client, auth_headers):
        """Test updating a non-existent person."""
        fake_id = str(uuid.uuid4())
        payload = {"first_name": "Updated"}
        response = client.patch(
            f"/people/{fake_id}", json=payload, headers=auth_headers
        )
        assert response.status_code == 404

    def test_delete_person(self, client, auth_headers, db_session):
        """Test deleting a person."""
        from app.models.person import Person

        # Create a person to delete
        person = Person(
            first_name="ToDelete",
            last_name="User",
            email=f"delete_{uuid.uuid4().hex[:8]}@example.com",
            organization_id=DEFAULT_TEST_ORG_ID,
        )
        db_session.add(person)
        db_session.commit()
        db_session.refresh(person)

        response = client.delete(f"/people/{person.id}", headers=auth_headers)
        assert response.status_code == 204

    def test_delete_person_not_found(self, client, auth_headers):
        """Test deleting a non-existent person."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/people/{fake_id}", headers=auth_headers)
        assert response.status_code == 404


class TestPersonsAPIV1:
    """Tests for the /api/v1/people endpoints."""

    def test_create_person_v1(self, client, auth_headers):
        """Test creating a person via v1 API."""
        payload = {
            "first_name": "V1",
            "last_name": "User",
            "email": f"v1_{uuid.uuid4().hex[:8]}@example.com",
            "organization_id": str(DEFAULT_TEST_ORG_ID),
        }
        response = client.post("/api/v1/people", json=payload, headers=auth_headers)
        assert response.status_code == 201

    def test_get_person_v1(self, client, auth_headers, person):
        """Test getting a person via v1 API."""
        response = client.get(f"/api/v1/people/{person.id}", headers=auth_headers)
        assert response.status_code == 200

    def test_list_people_v1(self, client, auth_headers):
        """Test listing people via v1 API."""
        response = client.get("/api/v1/people", headers=auth_headers)
        assert response.status_code == 200
