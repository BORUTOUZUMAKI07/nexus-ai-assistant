"""
Unit tests for the organization / workspace layer: creation, membership roles,
email invites + acceptance (email-match enforced), leave/remove, and owner-gated
deletion — all against in-memory fakes.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from backend.app.domain.org.models import Organization, OrganizationInvite, OrganizationMember
from backend.app.domain.user.models import User
from backend.app.services import org_service as org_module
from backend.app.services.org_service import OrganizationService
from fakes import FakeSession


def _await(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _user(email):
    return User(email=email, username=email.split("@")[0])


def _seed_org(fake, owner, name="Acme Corp", slug=None):
    return _await(OrganizationService(fake).create(owner, name, slug))


def test_create_org_adds_owner_membership():
    owner = uuid4()
    fake = FakeSession()
    org = _await(OrganizationService(fake).create(owner, "Acme Corp"))
    assert org.slug == "acme-corp"
    assert org.owner_id == owner
    members = fake.rows.get(OrganizationMember, [])
    assert len(members) == 1
    assert members[0].user_id == owner
    assert members[0].role == "owner"


def test_create_org_duplicate_slug_rejected():
    owner = uuid4()
    fake = FakeSession()
    _seed_org(fake, owner)
    with pytest.raises(ValueError):
        _await(OrganizationService(fake).create(owner, "Acme Corp!"))


def test_list_my_orgs_scoped_by_membership():
    user_a, user_b = uuid4(), uuid4()
    fake = FakeSession()
    fake.seed(User, [_user("a@example.com"), _user("b@example.com")])
    org_a = _seed_org(fake, user_a, "Alpha")
    org_b = _seed_org(fake, user_b, "Beta")
    # user A is also a plain member of org B
    fake.add(OrganizationMember(organization_id=org_b.id, user_id=user_a, role="member"))

    fake.add_hook(
        Organization,
        lambda orgs: [
            (o, "owner" if o.id == org_a.id else "member")
            for o in orgs
            if any(
                m.user_id == user_a and m.organization_id == o.id
                for m in fake.rows.get(OrganizationMember, [])
            )
        ],
    )

    mine = _await(OrganizationService(fake).list_my(user_a))
    assert {o["slug"] for o in mine} == {"alpha", "beta"}
    by_slug = {o["slug"]: o["role"] for o in mine}
    assert by_slug["alpha"] == "owner"
    assert by_slug["beta"] == "member"


def test_invite_requires_manage_role():
    owner, member = uuid4(), uuid4()
    fake = FakeSession()
    org = _seed_org(fake, owner)
    fake.add(OrganizationMember(organization_id=org.id, user_id=member, role="member"))
    svc = OrganizationService(fake)

    with pytest.raises(PermissionError):
        _await(svc.invite(org.id, member, "colleague@example.com"))

    invite = _await(svc.invite(org.id, owner, "colleague@example.com", role="owner"))
    assert invite is not None
    assert invite.role == "member"  # "owner" is coerced to the allowed set
    assert invite.status == "pending"

    with pytest.raises(ValueError):
        _await(svc.invite(org.id, owner, "colleague@example.com"))  # duplicate pending


def test_accept_invite_requires_matching_email():
    owner = uuid4()
    fake = FakeSession()
    org = _seed_org(fake, owner)
    colleague_user = _user("colleague@example.com")
    stranger_user = _user("stranger@example.com")
    fake.seed(User, [colleague_user, stranger_user])
    invite = _await(
        OrganizationService(fake).invite(org.id, owner, colleague_user.email)
    )
    svc = OrganizationService(fake)

    # Wrong email cannot join: invite is addressed to colleague.
    with pytest.raises(PermissionError):
        _await(svc.accept_invite(invite.token, stranger_user.id))

    joined = _await(svc.accept_invite(invite.token, colleague_user.id))
    assert joined is not None
    assert joined.id == org.id
    member_rows = fake.rows.get(OrganizationMember, [])
    assert any(m.user_id == colleague_user.id and m.role == "member" for m in member_rows)
    assert invite.status == "accepted"

    # Already-used invite is rejected.
    assert _await(svc.accept_invite(invite.token, colleague_user.id)) is None


def test_accept_invite_expired_or_unknown():
    fake = FakeSession()
    expired = OrganizationInvite(
        organization_id=uuid4(),
        invited_by=uuid4(),
        email="x@example.com",
        role="member",
        token="tok-expired",
        status="pending",
        expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
    )
    fake.seed(OrganizationInvite, [expired])
    svc = OrganizationService(fake)
    assert _await(svc.accept_invite("tok-expired", uuid4())) is None
    assert _await(svc.accept_invite("tok-unknown", uuid4())) is None


def test_remove_member_rules():
    owner, admin, member = uuid4(), uuid4(), uuid4()
    fake = FakeSession()
    org = _seed_org(fake, owner)
    fake.add(OrganizationMember(organization_id=org.id, user_id=admin, role="admin"))
    fake.add(OrganizationMember(organization_id=org.id, user_id=member, role="member"))
    svc = OrganizationService(fake)

    with pytest.raises(ValueError):
        _await(svc.remove_member(org.id, owner, owner))  # can't remove the owner

    with pytest.raises(PermissionError):
        _await(svc.remove_member(org.id, member, admin))  # member has no manage role

    assert _await(svc.remove_member(org.id, admin, member)) is True
    assert not any(
        m.user_id == member for m in fake.rows.get(OrganizationMember, [])
    )


def test_leave_org_rules():
    owner, member = uuid4(), uuid4()
    fake = FakeSession()
    org = _seed_org(fake, owner)
    fake.add(OrganizationMember(organization_id=org.id, user_id=member, role="member"))
    svc = OrganizationService(fake)

    with pytest.raises(ValueError):
        _await(svc.leave(org.id, owner))  # owner must transfer/delete instead

    assert _await(svc.leave(org.id, member)) is True
    assert not any(
        m.user_id == member for m in fake.rows.get(OrganizationMember, [])
    )


def test_delete_org_is_owner_only():
    owner, member = uuid4(), uuid4()
    fake = FakeSession()
    org = _seed_org(fake, owner)
    fake.add(OrganizationMember(organization_id=org.id, user_id=member, role="member"))
    fake.add(
        OrganizationInvite(
            organization_id=org.id,
            invited_by=owner,
            email="pending@example.com",
            role="member",
            token="tok-pending",
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1),
        )
    )
    svc = OrganizationService(fake)

    with pytest.raises(PermissionError):
        _await(svc.delete(org.id, member))

    assert _await(svc.delete(org.id, owner)) is True
    assert fake.rows.get(Organization, []) == []
    assert fake.rows.get(OrganizationMember, []) == []
    assert fake.rows.get(OrganizationInvite, []) == []