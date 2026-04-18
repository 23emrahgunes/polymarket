# Polymarket Research V2 VPS Rollout

Bu yuzey legacy `/root/polymarket` runtime'ina dokunmadan ayri bir klasorde kurulur:

- uygulama dizini: `/root/polymarket-research-v2`
- dashboard portu: `8082`
- veritabani: `data/research_v2.db`
- lane mode: `polymarket_research`

## Gerekli ortam degiskenleri

Asagidaki degiskenler bootstrap oncesi export edilebilir:

```bash
export REPO_URL="https://github.com/23emrahgunes/polymarket.git"
export REPO_REF="codex/clean-split-rebuild-v1"
export LEGACY_DB_PATH="/root/polymarket/data/ghost_trader.db"
```

Private repo kullaniyorsan `REPO_URL` icine PAT ile erisen URL ver.

## Kurulum

Repo VPS'te zaten varsa:

```bash
cd /root/polymarket
git fetch origin
git checkout codex/clean-split-rebuild-v1
git pull --ff-only origin codex/clean-split-rebuild-v1
```

Ardindan bootstrap:

```bash
cd /root/polymarket
bash scripts/bootstrap_research_v2_vps.sh
```

Bootstrap su isleri yapar:

1. `/root/polymarket-research-v2` altina repo klonlar veya gunceller
2. `.venv` olusturur ve dependency kurar
3. `.env` icine research-v2 ayarlarini yazar
4. legacy DB varsa research seed import yapar
5. research summary uretir
6. ayri dashboard service + refresh timer kurar

## Kurulum sonrasi kontrol

```bash
sudo systemctl status ghost-trader-research-dashboard --no-pager
sudo systemctl status ghost-trader-research-refresh.timer --no-pager
```

Manuel summary:

```bash
cd /root/polymarket-research-v2
source .venv/bin/activate
python scripts/query_polymarket_research.py summary --db-path data/research_v2.db
```

Dashboard:

```text
http://SERVER_IP:8082/
```

## Beklenen davranis

- legacy bot ve legacy dashboard ayni yerde calismaya devam eder
- research-v2 lane trade acmaz
- sadece discovery / shadow / copy-ready research yuzeyi sunar
- Binance technical sekmesi research-v2 dashboard HTML'inde render edilmez
