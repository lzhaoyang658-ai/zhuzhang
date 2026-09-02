import pytest
from sqlalchemy import select

from app.models import Project, ProjectMembership, User
from app.services.seed import assert_production_demo_isolation, seed_demo


def project(name: str = "真实项目") -> Project:
    return Project(
        name=name,
        city="上海",
        area_sqm=80,
        area_basis="建筑面积",
        renovation_type="半包",
        fund_limit_cents=200_000_00,
        reserve_cents=20_000_00,
        status="准备中",
    )


def test_seed_demo_creates_demo_only_for_an_empty_database(db_session):
    assert seed_demo(db_session) is True
    seeded = db_session.scalar(select(Project))
    assert seeded is not None
    membership = db_session.scalar(select(ProjectMembership).where(ProjectMembership.project_id == seeded.id))
    assert membership is not None
    assert membership.user_id == "demo-owner"
    assert membership.role == "owner"
    assert seed_demo(db_session) is False
    assert len(list(db_session.scalars(select(Project)).all())) == 1


def test_seed_demo_never_grants_demo_owner_access_to_existing_projects(db_session):
    real_owner = User(id="real-owner", name="真实业主", email="real-owner@example.com")
    existing = project()
    db_session.add_all([real_owner, existing])
    db_session.flush()
    db_session.add(ProjectMembership(user_id=real_owner.id, project_id=existing.id, role="owner"))
    db_session.commit()

    assert seed_demo(db_session) is False
    demo_membership = db_session.scalar(select(ProjectMembership).where(
        ProjectMembership.project_id == existing.id,
        ProjectMembership.user_id == "demo-owner",
    ))
    assert demo_membership is None


def test_production_preflight_rejects_an_active_demo_identity(db_session):
    with pytest.raises(RuntimeError, match="demo-owner"):
        assert_production_demo_isolation(db_session)

    owner = db_session.get(User, "demo-owner")
    owner.status = "disabled"
    db_session.commit()
    assert_production_demo_isolation(db_session)
