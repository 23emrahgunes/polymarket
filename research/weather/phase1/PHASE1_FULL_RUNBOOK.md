# Polymarket Weather — Full Phase 1

## Amaç

Resolved günlük sıcaklık marketlerinde trader bağımsız calibration testi:

`calibration_gap = empirical_win_rate - mean_market_price`

Pozitif gap tek başına edge değildir. Yeterli bağımsız city-day, cluster-bootstrap CI ve outcome henüz bilinemezken alınmış ex-ante timestamp gerekir.

## Kurulum

```bash
cd ~/polymarket/research/weather/phase1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Smoke test

```bash
python phase1_full_runner.py \
  --start 2026-07-01 \
  --end 2026-07-07 \
  --max-events 10 \
  --out phase1_smoke
```

Kontrol:

```bash
cat phase1_smoke/quality_summary.csv
cat phase1_smoke/REPORT.md
```

## Full run

```bash
chmod +x run_phase1_full.sh
./run_phase1_full.sh 2026-03-01 2026-08-08 phase1_full_out 2>&1 | tee phase1_full.log
```

## Ana çıktılar

- `events.csv`
- `markets.csv`
- `observations.csv`
- `calibration.csv`
- `calibration_by_city.csv`
- `calibration_by_kind.csv`
- `quality_summary.csv`
- `summary.json`
- `REPORT.md`

## Kritik uyarı

İlk geniş tarama Gamma `event.endDate` değerini anchor olarak kullanır. Bu yalnızca keşif taramasıdır. Günlük max/min sıcaklık outcome'u resmi resolution'dan önce fiilen bilinebilir hale gelebilir. Phase 1'de pozitif görünen şehir/horizon hücreleri, Phase 2 öncesinde exact settlement station + local day cutoff kullanılarak yeniden test edilmelidir.

Bu yüzden ilk broad scan sonucu doğrudan canlı trading kuralı değildir.
