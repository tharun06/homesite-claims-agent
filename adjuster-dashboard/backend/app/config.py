"""Central config. Mock-JWT secret for Phase 1; swap for real IdP later."""

JWT_SECRET    = "dev-secret-not-for-prod"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 12
