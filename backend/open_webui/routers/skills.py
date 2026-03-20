import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from open_webui.models.groups import Groups
from pydantic import BaseModel

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from open_webui.internal.db import get_session
from open_webui.models.skills import (
    SkillForm,
    SkillMeta,
    SkillModel,
    SkillResponse,
    SkillUserResponse,
    SkillAccessResponse,
    SkillAccessListResponse,
    Skills,
)
from open_webui.models.access_grants import AccessGrants
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_permission, filter_allowed_access_grants

from open_webui.config import BYPASS_ADMIN_ACCESS_CONTROL
from open_webui.constants import ERROR_MESSAGES

log = logging.getLogger(__name__)

PAGE_ITEM_COUNT = 30

router = APIRouter()


############################
# GetSkills
############################


@router.get("/", response_model=list[SkillUserResponse])
async def get_skills(
    request: Request,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
        skills = Skills.get_skills(db=db)
    else:
        user_group_ids = {
            group.id for group in Groups.get_groups_by_member_id(user.id, db=db)
        }
        all_skills = Skills.get_skills(db=db)
        skills = [
            skill
            for skill in all_skills
            if skill.user_id == user.id
            or AccessGrants.has_access(
                user_id=user.id,
                resource_type="skill",
                resource_id=skill.id,
                permission="read",
                user_group_ids=user_group_ids,
                db=db,
            )
        ]

    return skills


############################
# GetSkillList
############################


@router.get("/list", response_model=SkillAccessListResponse)
async def get_skill_list(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    limit = PAGE_ITEM_COUNT

    page = max(1, page)
    skip = (page - 1) * limit

    filter = {}
    if query:
        filter["query"] = query
    if view_option:
        filter["view_option"] = view_option

    if not (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL):
        groups = Groups.get_groups_by_member_id(user.id, db=db)
        if groups:
            filter["group_ids"] = [group.id for group in groups]

        filter["user_id"] = user.id

    result = Skills.search_skills(user.id, filter=filter, skip=skip, limit=limit, db=db)

    return SkillAccessListResponse(
        items=[
            SkillAccessResponse(
                **skill.model_dump(),
                write_access=(
                    (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
                    or user.id == skill.user_id
                    or AccessGrants.has_access(
                        user_id=user.id,
                        resource_type="skill",
                        resource_id=skill.id,
                        permission="write",
                        db=db,
                    )
                ),
            )
            for skill in result.items
        ],
        total=result.total,
    )


############################
# ExportSkills
############################


@router.get("/export", response_model=list[SkillModel])
async def export_skills(
    request: Request,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    if user.role != "admin" and not has_permission(
        user.id,
        "workspace.skills",
        request.app.state.config.USER_PERMISSIONS,
        db=db,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    if user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL:
        return Skills.get_skills(db=db)
    else:
        return Skills.get_skills_by_user_id(user.id, "read", db=db)


############################
# CreateNewSkill
############################


@router.post("/create", response_model=Optional[SkillResponse])
async def create_new_skill(
    request: Request,
    form_data: SkillForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.skills", request.app.state.config.USER_PERMISSIONS, db=db
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    form_data.id = form_data.id.lower().replace(" ", "-")

    existing = Skills.get_skill_by_id(form_data.id, db=db)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )

    try:
        skill = Skills.insert_new_skill(user.id, form_data, db=db)
        if skill:
            return skill
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error creating skill"),
            )
    except Exception as e:
        log.exception(f"Failed to create skill: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# UploadSkillZip
############################

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50MB


def _slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")


def _parse_skill_md_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from SKILL.md content."""
    import yaml

    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


@router.post("/upload-zip", response_model=Optional[SkillResponse])
async def upload_skill_zip(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.skills", request.app.state.config.USER_PERMISSIONS, db=db
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    # Validate file
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .zip files are accepted",
        )

    # Read file content (limit to MAX_ZIP_SIZE)
    content = await file.read()
    if len(content) > MAX_ZIP_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {MAX_ZIP_SIZE // (1024*1024)}MB",
        )

    # Write to temp file for zipfile validation
    tmp_zip = None
    tmp_extract = None
    try:
        tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_zip.write(content)
        tmp_zip.close()

        if not zipfile.is_zipfile(tmp_zip.name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid zip file",
            )

        # Check uncompressed size (zip bomb protection)
        with zipfile.ZipFile(tmp_zip.name, "r") as zf:
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > MAX_ZIP_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Uncompressed size too large ({total_size // (1024*1024)}MB). Maximum is {MAX_ZIP_SIZE // (1024*1024)}MB",
                )

        # Extract to temp dir
        tmp_extract = tempfile.mkdtemp(prefix="skill-zip-")
        with zipfile.ZipFile(tmp_zip.name, "r") as zf:
            zf.extractall(tmp_extract)

        # Find SKILL.md (root or one level deep)
        skill_md_path = None
        skill_root = None
        extract_path = Path(tmp_extract)

        # Check root level
        if (extract_path / "SKILL.md").exists():
            skill_md_path = extract_path / "SKILL.md"
            skill_root = extract_path
        else:
            # Check one level deep (common when zip contains a directory)
            for child in extract_path.iterdir():
                if child.is_dir() and (child / "SKILL.md").exists():
                    skill_md_path = child / "SKILL.md"
                    skill_root = child
                    break

        if not skill_md_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No SKILL.md found in zip archive (checked root and one level deep)",
            )

        # Parse frontmatter from SKILL.md
        skill_content = skill_md_path.read_text(encoding="utf-8")
        frontmatter = _parse_skill_md_frontmatter(skill_content)

        skill_name = frontmatter.get("name", "")
        if not skill_name:
            # Fall back to directory name or zip filename
            skill_name = skill_root.name if skill_root != extract_path else file.filename.replace(".zip", "")

        description = frontmatter.get("description", "")

        # Generate skill ID
        skill_id = _slugify(skill_name)
        if not skill_id:
            skill_id = f"agent-skill-{int(time.time())}"

        # Check for existing skill
        import time

        existing = Skills.get_skill_by_id(skill_id, db=db)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.ID_TAKEN,
            )

        # Copy to persistent storage
        persistent_dir = Path.home() / ".claude" / "skills" / skill_id
        persistent_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(skill_root), str(persistent_dir), dirs_exist_ok=True)

        # Create DB record
        form_data = SkillForm(
            id=skill_id,
            name=skill_name,
            description=description,
            content=skill_content,
            meta=SkillMeta(
                type="agent_skill",
                disk_path=str(persistent_dir),
                tags=frontmatter.get("tags", []) or [],
            ),
            is_active=True,
        )

        skill = Skills.insert_new_skill(user.id, form_data, db=db)
        if skill:
            return skill
        else:
            # Cleanup on failure
            shutil.rmtree(str(persistent_dir), ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error creating agent skill"),
            )

    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Failed to upload agent skill zip: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )
    finally:
        # Cleanup temp files
        if tmp_zip and os.path.exists(tmp_zip.name):
            os.unlink(tmp_zip.name)
        if tmp_extract and os.path.exists(tmp_extract):
            shutil.rmtree(tmp_extract, ignore_errors=True)


############################
# GetSkillById
############################


@router.get("/id/{id}", response_model=Optional[SkillAccessResponse])
async def get_skill_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    skill = Skills.get_skill_by_id(id, db=db)

    if skill:
        if (
            user.role == "admin"
            or skill.user_id == user.id
            or AccessGrants.has_access(
                user_id=user.id,
                resource_type="skill",
                resource_id=skill.id,
                permission="read",
                db=db,
            )
        ):
            return SkillAccessResponse(
                **skill.model_dump(),
                write_access=(
                    (user.role == "admin" and BYPASS_ADMIN_ACCESS_CONTROL)
                    or user.id == skill.user_id
                    or AccessGrants.has_access(
                        user_id=user.id,
                        resource_type="skill",
                        resource_id=skill.id,
                        permission="write",
                        db=db,
                    )
                ),
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateSkillById
############################


@router.post("/id/{id}/update", response_model=Optional[SkillModel])
async def update_skill_by_id(
    request: Request,
    id: str,
    form_data: SkillForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    skill = Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="skill",
            resource_id=skill.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    try:
        updated = {
            **form_data.model_dump(exclude={"id"}),
        }

        skill = Skills.update_skill_by_id(id, updated, db=db)

        if skill:
            return skill
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating skill"),
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# UpdateSkillAccessById
############################


class SkillAccessGrantsForm(BaseModel):
    access_grants: list[dict]


@router.post("/id/{id}/access/update", response_model=Optional[SkillModel])
async def update_skill_access_by_id(
    request: Request,
    id: str,
    form_data: SkillAccessGrantsForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    skill = Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="skill",
            resource_id=skill.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    form_data.access_grants = filter_allowed_access_grants(
        request.app.state.config.USER_PERMISSIONS,
        user.id,
        user.role,
        form_data.access_grants,
        "sharing.public_skills",
    )

    AccessGrants.set_access_grants("skill", id, form_data.access_grants, db=db)

    return Skills.get_skill_by_id(id, db=db)


############################
# ToggleSkillById
############################


@router.post("/id/{id}/toggle", response_model=Optional[SkillModel])
async def toggle_skill_by_id(
    id: str, user=Depends(get_verified_user), db: Session = Depends(get_session)
):
    skill = Skills.get_skill_by_id(id, db=db)
    if skill:
        if (
            user.role == "admin"
            or skill.user_id == user.id
            or AccessGrants.has_access(
                user_id=user.id,
                resource_type="skill",
                resource_id=skill.id,
                permission="write",
                db=db,
            )
        ):
            skill = Skills.toggle_skill_by_id(id, db=db)

            if skill:
                return skill
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error toggling skill"),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.UNAUTHORIZED,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# DeleteSkillById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_skill_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    skill = Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if (
        skill.user_id != user.id
        and not AccessGrants.has_access(
            user_id=user.id,
            resource_type="skill",
            resource_id=skill.id,
            permission="write",
            db=db,
        )
        and user.role != "admin"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    # Clean up disk files for agent skills
    if skill.meta and isinstance(skill.meta, dict):
        meta = skill.meta
    else:
        meta = skill.meta.model_dump() if hasattr(skill.meta, "model_dump") else {}

    if meta.get("type") == "agent_skill" and meta.get("disk_path"):
        try:
            disk_path = meta["disk_path"]
            if os.path.isdir(disk_path):
                shutil.rmtree(disk_path, ignore_errors=True)
                log.info(f"Cleaned up agent skill files at {disk_path}")
        except Exception as e:
            log.warning(f"Failed to cleanup agent skill disk path: {e}")

    result = Skills.delete_skill_by_id(id, db=db)
    return result
