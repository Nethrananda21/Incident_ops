# IncidentOps AI Supporting Documents

## Included Documents

- `README.md` - full technical overview, architecture, API reference, setup, testing, and demo flow.
- `DESIGN.md` - product design direction and implementation notes.
- `frontend/design.md` - frontend design system, theme, layout, and UX guidance.
- `docs/PRODUCT_USER_GUIDE.md` - end-user guide for running and using the product.
- `docs/ARCHITECTURE_DIAGRAM.md` - visual architecture diagram and implementation notes.
- `docs/architecture-diagram.svg` - standalone architecture diagram image.
- `docs/architecture-diagram.png` - portable rendered copy of the architecture diagram.

## Recommended Submission Attachments

- Product User Guide: `docs/PRODUCT_USER_GUIDE.md`
- Optional Supporting Documents: `README.md`, `DESIGN.md`, `frontend/design.md`, `docs/ARCHITECTURE_DIAGRAM.md`, `docs/architecture-diagram.svg`, `docs/architecture-diagram.png`, and this file.

## Verification Notes

The project is Dockerized and intended to run locally with:

```powershell
docker compose up -d --build
```

The main frontend entry point is:

```text
http://localhost:8081/dashboard.html
```
