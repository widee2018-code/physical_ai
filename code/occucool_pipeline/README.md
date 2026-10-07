# OccuCool 전처리·제어 파이프라인

현장 맞춤 전처리 설계안(보고서)의 파이프라인을 그대로 구현한 코드입니다. 코드 주석의 `[V1]`, `[S3]` 같은 표시는 보고서 2장의 단계 번호와 같습니다.

```
0 → [V1→…→V9] ∥ [S1→…→S6] ∥ [A1→A2→A3] ∥ [O1, O2] ∥ [E1→E2] → M1 → M2 → M3 → (M4) → P1
```

## 실행

```bash
pip install numpy opencv-python          # 필수 (opencv는 ROI 마스크·왜곡 보정용, 없어도 동작)
python demo_simulation.py                # 카메라·센서 없이 전체 파이프라인 시뮬레이션
python -m pytest -q                      # 단위 테스트 12개
python examples/run_edge.py              # 실제 장비용 골격 (ultralytics 필요, TODO 구현 후)
```

## 파일 구조와 단계 매핑

| 파일 | 단계 | 내용 |
|---|---|---|
| `occucool/config.py` | 전체 | 보고서 3장 파라미터 기본값. 보고서에 없던 값은 `가정`으로 표시 |
| `occucool/vision.py` | V1~V6 | 노출 고정, ROI 마스크, 적응형 샘플링, 타일 검출, 후처리 + 프레임 폐기, IoU 추적, 발 위치 투영·구역 할당, 출입구 기반 유령 유지 |
| `occucool/occupancy.py` | V7~V9 | 중앙값·Hampel, 10초 리샘플링, 비대칭 Debounce 상태 머신, 재실 단계 히스테리시스 |
| `occucool/sensors.py` | S1~S6 | 범위·변화율·Hampel, 결측 계층 처리 + 사후 보간, 오프셋, EMA, 이슬점·절대습도·dT/dt |
| `occucool/hvac.py` | A1~A3 | 계단 함수 상태, 명령-피드백 대조·재전송·알림, 수동 조작 감지, 전력 교차 검증, 데드타임 추정 |
| `occucool/external.py` | O1~O2 | 외기 정제, 운영 스케줄·예냉 |
| `occucool/energy.py` | E1~E2 | 1분 전력·Wh·운전 상태 분류, CDD·재실시간 기준선 회귀 |
| `occucool/fusion.py` | M1~M4 | 그리드 레코드, 안전 모드 판정, 1분 제어 집계, 정규화기 |
| `occucool/control.py` | 제어, P1 | 규칙 기반 판단(Setback → OFF 2단계, 제습), 출력 보호 |
| `occucool/pipeline.py` | 0, 전체 | 오케스트레이터 |

## 보고서에서 정하지 않아 코드에서 정한 값 (현장 확인 필요)

`config.py`에서 `가정` 주석으로 모두 찾을 수 있습니다. 주요 항목은 기본 설정온도(냉방 24℃), 3명 이상 시 강화폭(1℃), Setback 후 OFF까지 시간(30분), 유령 유지 시간(5분), 운영 시간(평일 09~18시), 에어컨 정격 전력(3.5kW)입니다.

## 설계상 판단 두 가지

1. 설정온도 변화율 제한은 기본적으로 불쾌 방향(냉방 중 설정온도 상승)에만 적용합니다. 재실자가 돌아왔을 때 쾌적 설정으로의 복귀가 10분씩 지연되지 않도록 하기 위함입니다. 양방향 제한이 필요하면 `rate_limit_both_directions=True`로 바꾸면 됩니다.
2. 온습도 S3의 선형 보간은 실시간에 불가능합니다(다음 샘플이 필요). 그래서 실시간에는 직전값을 `INTERP` 플래그와 함께 내보내 제습 등 모드 전환을 막고, 통신 복구 후 `backfill` 이벤트로 보간값을 기록합니다.
