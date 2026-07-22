# Windsor Widget 2.0 web foundation

## Architecture

The application is hosted once on the office network. Users connect with a browser; no
client installation is required.

- FastAPI application server
- Jinja templates and local JavaScript/CSS assets
- SQLAlchemy services and SQL Server Express
- Signed browser sessions
- Argon2id password hashes
- Individual application users with `admin`, `procurement` or `read_only` roles
- Three client-side visual schemes: Windsor, Light and Dark

## Theme system

The selected theme is stored in the browser's local storage and does not alter business
data. Windsor is the default. Its red and charcoal palette is based on the supplied Windsor
logo and the clean white/charcoal presentation of the Windsor Trading website.

## Development start

```powershell
.\scripts\web_workflow.ps1 -Action Install
.\scripts\web_workflow.ps1 -Action Migrate
.\scripts\web_workflow.ps1 -Action CreateAdmin -Username "brad" -DisplayName "Brad Mayze"
.\scripts\web_workflow.ps1 -Action Run
```

Open `http://localhost:8080` on the server or `http://COMPUTERNAME:8080` from another PC.

## Security boundary

This phase is designed for a trusted private LAN. Do not expose port 8080 directly to the
public internet. Remote access should later use a VPN or Tailscale and HTTPS.

## Next web phase

- Item Summary and customer drill-down routes
- Browser import/review/approval workflow
- User administration screen
- operational notes and optimistic locking
- Windows service deployment and firewall automation
