Review code for security issues using the ATLAS Security Best Practices skill.

Follow the security-best-practices SKILL.md protocol:
1. Run OWASP Top 10 checklist against the code
2. Check: parameterized queries, input validation, output encoding
3. Check: secret management (env vars, not hardcoded)
4. Check: auth patterns (bcrypt, JWT, session security)
5. Check: HTTPS, security headers, CORS, CSRF protection
6. Report findings with severity levels

Constraints (MUST):
- Parameterized queries for ALL database operations
- bcrypt for passwords (cost >= 12)
- HTTPS in production
- Server-side input validation
- Security headers on all responses
- Log all authentication events

Constraints (MUST NOT):
- Store secrets in source code
- Use MD5/SHA1 for password hashing
- Disable SSL verification
- Expose stack traces in production
- Trust client-side validation alone

Reference: atlas/skills/library/security-best-practices/SKILL.md
