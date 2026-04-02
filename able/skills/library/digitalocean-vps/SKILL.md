# Digital Ocean VPS Skill

## Purpose
Provision new Digital Ocean droplets for isolated server environments: security labs, Kali Linux instances, resource-intensive services, or isolated client environments.

## When to Use
Trigger when the user says:
- "new server", "provision a server", "spin up a VPS"
- "create a droplet", "new linux machine"
- "Kali", "new environment", "set up a server"
- "I need a separate machine for..."

## When NOT to Use
- Simple static hosting → Use GitHub Pages
- Frontend/API deployment → Use Vercel
- Adding capacity to existing infrastructure → Modify existing droplet instead

## Protocol

### Defaults (use unless user specifies otherwise)
| Setting | Default |
|---|---|
| Image | `ubuntu-24-04-x64` |
| Region | `nyc3` (New York) |
| Size | `s-1vcpu-1gb` ($6/month) |
| Monitoring | enabled |
| Backups | disabled (cost savings) |

### Region Selection
- `nyc3` — default, general use
- `sfo3` — US West Coast
- `ams3` — Europe
- `sgp1` — Asia Pacific
- `lon1` — UK

### Size Selection
| Size slug | vCPU | RAM | Use case |
|---|---|---|---|
| `s-1vcpu-1gb` | 1 | 1GB | Light tasks, testing |
| `s-2vcpu-2gb` | 2 | 2GB | Dev environments |
| `s-4vcpu-8gb` | 4 | 8GB | Medium workloads |
| `c-4` | 4 | 8GB | CPU-optimized |

### Kali Linux
- Image: `kali-linux-2024-1-x64` (if available in region, else use Ubuntu + install Kali tools)
- Always private: never expose Kali directly to public without firewall config
- Recommend adding SSH key before provisioning

### SSH Key Management
- Always provision with at least one SSH key
- Add user's public key via `DigitalOceanClient.add_ssh_key()` before creating droplet
- Hand back: `ssh root@{ip}` with key path

### Handing Back Credentials
After provisioning, return to user:
```
✅ Droplet ready

Name: {name}
IP: {public_ip}
Region: {region}
Size: {size} ({cost}/month)

SSH: ssh root@{public_ip}
```

## Approval Required
HIGH RISK — creates billable infrastructure. Always requires owner approval.
Show cost estimate before requesting approval.
