import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_current_seller
from packages.db.models import Item, ItemImage, Seller
from packages.db.session import get_session
from packages.storage import upload_image

router = APIRouter(prefix="/agent/intake", tags=["intake"])

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED = {"image/jpeg", "image/png", "image/gif", "image/webp"}


@router.post("/upload-image")
async def upload_item_image(
    item_id: uuid.UUID = Query(...),  # noqa: B008
    file: UploadFile = File(...),  # noqa: B008
    seller: Seller = Depends(get_current_seller),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict:
    if file.content_type not in _ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported type '{file.content_type}'. Use JPEG, PNG, GIF, or WebP.",
        )

    data = await file.read()
    if len(data) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds 10 MB limit.",
        )

    item = await session.scalar(select(Item).where(Item.id == item_id))
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found.")

    position = await session.scalar(select(func.count()).where(ItemImage.item_id == item_id)) or 0

    s3_key, url = await upload_image(data, file.filename or "image.jpg", seller.id, item_id)

    image = ItemImage(
        item_id=item_id,
        seller_id=seller.id,
        s3_key=s3_key,
        url=url,
        position=position,
    )
    session.add(image)
    await session.commit()
    await session.refresh(image)

    return {"id": str(image.id), "url": url, "position": image.position}
