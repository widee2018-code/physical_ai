# OccuCool SBC 컨테이너 구조 설계 리포트

> `index.html`에 정리된 OccuCool 제품 사양(How it works · 전처리 · 후처리 · AI 모델 · 추론 엔진 · 프라이버시 · Architecture · Hardware)을 기준으로, 메인 SoC(i.MX 8M Plus급 SBC)에서 제품을 **효율적이고 안정적으로** 운용하기 위한 컨테이너 구조를 제안
>
> 작성일: 2026-10-16
> 참고 자료: `index.html`, `report/hardware_report.md`, `report/preprocess_report.md`, `report/postprocess_report.md`, `report/inference_engine_report.md`, `report/model_selection_report.md`

---

## 0. 결론 요약

SoC 위에 **상시 컨테이너 6개 + 설치 시에만 뜨는 컨테이너 1개**를 둡니다. 실시간 안전 제어는 컨테이너가 아닌 **MCU 펌웨어**에 그대로 남깁니다.

| # | 컨테이너 | 맡는 일 (index.html 단계 기준) | 네트워크 | 재시작 우선순위 |
|---|---|---|---|---|
| 1 | **`occu-broker`** | 기기 내부 메시지 버스 (Mosquitto, Unix 소켓) | 없음 | 최상 |
| 2 | **`occu-vision`** | 카메라 캡처 → NPU 추론 → 추적 → 카운트 (V1~V7, D1~D5) | **없음** | 최상 |
| 3 | **`occu-control`** | 센서 정제 · 재실 판정 · 제어 결정 (S1~S6, A1~A3, O1~O2, V8~V9, M1~M3, C1, K1~K2, G1, P1) | 없음 | 최상 |
| 4 | **`occu-deviceio`** | MCU UART 브리지 · BACnet/IP · 하트비트 중계 | 사내망(BACnet)만 | 최상 |
| 5 | **`occu-uplink`** | 외부 전송 검사 · 오프라인 버퍼 · AWS IoT 연결 (L1, N2) | **외부 8883만** | 상 |
| 6 | **`occu-insight`** | 알림 정리 · KPI · 오작동 의심 구간 (N1, L2, L3) | 없음 | 하 (메모리 부족 시 먼저 종료) |
| 7 | `occu-provision` | BLE 페어링 · Wi-Fi 설정 · ROI/Homography 보정 | BLE | 설치 모드에서만 실행 |
| — | **MCU 펌웨어** (컨테이너 아님) | 압축기 3분 보호 · 시간당 4회 제한 · IR/RS-485 · SoC 워치독 | — | SoC와 무관하게 상시 |

핵심 원칙 세 가지:

1. **영상은 `occu-vision` 밖으로 나갈 수 없다** — 이 컨테이너는 네트워크와 쓰기 가능한 디스크가 모두 없습니다. "영상을 안 보낸다"를 구조로 보장합니다(index.html 프라이버시 · L1 항목).
2. **장애는 번지지 않는다** — 영상, 판단, 장치 입출력, 클라우드 통신을 나눠, 하나가 죽어도 나머지가 index.html의 결측 처리 규칙대로 버팁니다.
3. **잘게 쪼개지 않는다** — LPDDR4 2GB·Cortex-A53 ×4 환경이라, 전처리 단계마다 컨테이너를 만들면 메모리와 지연만 늘어납니다. 같은 데이터를 같은 주기로 다루는 단계는 한 컨테이너로 묶습니다.

---

## 1. 설계 전제 (index.html에서 가져온 조건)

| 구분 | 조건 | 컨테이너 설계에 주는 영향 |
|---|---|---|
| 연산 | i.MX 8M Plus, Cortex-A53 ×4, NPU 2.3 TOPS | 컨테이너 런타임 자체의 오버헤드가 작아야 함. CPU 코어 배분 필요 |
| 메모리 | LPDDR4 2GB | 컨테이너 수를 한 자릿수로 제한, 컨테이너별 메모리 상한 필수 |
| 저장 | eMMC 16~32GB | A/B 이중화 + 이미지 2세대 보관 + 24시간 오프라인 로그 |
| 추론 | YOLO11n TFLite INT8 (~3MB), VX 델리게이트, 5fps · ≤200ms | NPU 장치 파일과 드라이버 사용자 공간 라이브러리를 컨테이너에 전달해야 함 |
| 제어 루프 | 감지 → 명령 5초 이내, 10초 그리드, 1분 집계 | 영상 → 판단 사이 경로가 짧아야 함 (IPC 1단계) |
| 프라이버시 | 원본 저장 불가, 외부 전송 0건, 숫자만 전송 | 영상을 다루는 컨테이너를 격리 |
| 신뢰성 | 24시간 오프라인 로컬 제어, 월 가동률 99.5%, 제어 성공률 99% | 자동 재시작, 헬스체크, 오프라인 버퍼, 업데이트 롤백 |
| 하드웨어 분리 | SoC = 영상·판단·통신·OTA, MCU = 실시간 제어·워치독 | 안전 규칙은 MCU가 최종 책임, 컨테이너는 "부드러운" 1차 보호만 |
| 통신 · 보안 | MQTT over TLS, X.509, 보안 부트, 서명 OTA | 인증서는 업링크 컨테이너만 접근, 이미지 서명 검증 |

---

## 2. 전체 구조

```
┌──────────────────────────── i.MX 8M Plus SoC (Linux) ───────────────────────────┐
│                                                                                 │
│  ┌──────────────┐   counts/tracks   ┌──────────────┐  cmd/state  ┌────────────┐ │
│  │ occu-vision  │ ─────────────────▶│              │◀───────────▶│occu-deviceio│─┼─UART(CRC16)─▶ MCU
│  │ CSI·NPU      │   (숫자만)        │ occu-broker  │             │ UART·BACnet │ │              (IR·RS-485·
│  │ net: none    │                   │  Mosquitto   │             └────────────┘ │               워치독·보호규칙)
│  └──────────────┘                   │ Unix socket  │                            │
│                                     │   + ACL      │  telemetry  ┌────────────┐ │
│  ┌──────────────┐  sensor/occupancy │              │────────────▶│occu-uplink │─┼─MQTT/TLS 8883─▶ AWS IoT Core
│  │ occu-control │◀─────────────────▶│              │             │L1 검사·N2버퍼│ │
│  │ 정제·판정·제어 │   decision/cmd    │              │             └────────────┘ │
│  └──────────────┘                   │              │  events     ┌────────────┐ │
│                                     │              │────────────▶│occu-insight│ │
│                                     └──────────────┘             │N1·L2·L3    │ │
│                                                                  └────────────┘ │
│  호스트: Yocto(NXP BSP) · 읽기 전용 rootfs · A/B 파티션 · systemd + Podman       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

데이터는 **영상 → 숫자 → 판단 → 명령 → 확인** 한 방향으로 흐르고, 컨테이너 사이에는 **브로커 하나만** 있습니다. 컨테이너끼리 직접 연결하지 않으므로, 하나를 교체·재시작해도 다른 컨테이너의 설정은 바뀌지 않습니다.

---

## 3. 컨테이너별 상세

### 3-1. `occu-vision` — 영상이 머무는 유일한 곳

| 항목 | 내용 |
|---|---|
| 포함 단계 | V1 캡처·ROI, V2 적응형 샘플링(재실 5fps / 공실 0.5fps), V3 YOLO11n 추론(TFLite INT8 + VX 델리게이트), **V4 박스 필터 + 프레임 폐기**, D1 영상 건전성, D2 저신뢰 2단계 연계, D3 기하 필터, D4 정적 오검출 맵, D5 타일 병합, V5 추적, V6 발 위치 보정·바닥 투영·구역 할당, V7 중앙값·Hampel |
| 출력 | `occu/vision/zone/{id}/count` (정수 + 품질 플래그 + **캡처 시각**), `occu/vision/health` (밝기·선명도·가림 점수, 숫자만) |
| 장치 | `/dev/video*`, `/dev/media*`(MIPI CSI), `/dev/galcore`(NPU) |
| 네트워크 | `none` — 브로커와는 Unix 소켓 볼륨으로만 통신 |
| 저장 | 읽기 전용 rootfs, `tmpfs`만 사용(크기 제한), **쓰기 가능한 영구 볼륨 없음** |
| 모델 | `/models/current` 읽기 전용 마운트 (4-3절) |
| 자원 | CPU 코어 2~3 고정, 메모리 상한 약 450MB |

**왜 V1~V7을 한 컨테이너에 묶는가**

- V4 이후 단계는 영상이 아닌 박스·트랙만 다루지만, 프레임 단위(5fps)로 돌아 컨테이너 사이로 보내면 IPC 비용과 지연이 늘어납니다.
- 반대로 V3와 V4를 나누면 **프레임이 컨테이너 경계를 넘게** 되어 프라이버시 경계가 흐려집니다. 프레임은 V4에서 폐기된 뒤 밖으로 나가는 것은 숫자뿐이어야 합니다.
- D1(영상 건전성)은 "프레임을 버리기 직전에 숫자로만 계산"해야 하므로 반드시 여기에 있어야 합니다.

**NPU를 컨테이너에서 쓸 때 주의점**

- VX 델리게이트(`libvx_delegate.so`)와 OpenVX/Galcore 사용자 공간 라이브러리는 **호스트 커널 드라이버 버전과 정확히 맞아야** 합니다. 베이스 이미지를 NXP BSP 버전(예: `imx-lf-6.x`)에 고정하고, 호스트 OS 업데이트와 vision 이미지 업데이트를 같은 릴리스 매트릭스로 관리합니다.
- NPU 그래프 컴파일 캐시(VIV_VX_CACHE)는 첫 실행이 느리므로, 캐시를 `tmpfs`가 아닌 **읽기 전용 모델 디렉터리 옆에 미리 생성**해 배포하면 재시작 후 첫 추론 지연을 줄일 수 있습니다.
- 실험 E0에서 C2PSA 블록이 CPU로 넘어가면 이 컨테이너만 YOLOv8n 이미지로 교체합니다. 다른 컨테이너는 그대로입니다.

### 3-2. `occu-control` — 제품의 두뇌

| 항목 | 내용 |
|---|---|
| 포함 단계 | 온습도 S1~S6, 에어컨 A1~A3, 외기·스케줄 O1~O2, V8 10초 리샘플링, V9 재실 상태 머신, M1 10초 병합, M2 안전 모드 판정, M3 1분 집계, C1 공실 확정 게이트, K1 인원 구간 → 설정온도·풍량, K2 이벤트 즉시 제어, G1 변경 빈도 제한, P1 출력 보호(1차) |
| 입력 | vision 카운트, deviceio의 센서·에어컨 상태, 운영 캘린더 |
| 출력 | `occu/control/command`(목표 전원·온도·풍량·모드 + 명령 ID), `occu/control/state`(재실 단계·안전 모드·품질 플래그) |
| 네트워크 | `none` |
| 저장 | `/data/control` 소용량 볼륨 — 상태 머신 스냅샷, 오프셋 보정값, 마지막 명령 (재시작 시 복원) |
| 자원 | CPU 코어 1, 메모리 상한 약 200MB |

**설계 포인트**

- 재실 판정과 제어 결정을 한 프로세스에 두는 이유: C1 공실 게이트는 D1·D2 결과, 트랙 상태, 출입구 퇴장 여부를 **같은 시각 기준으로** 함께 봐야 합니다. 나누면 시각 정렬 문제가 다시 생깁니다.
- **재시작 시 보수적으로 시작**: 스냅샷이 없거나 오래됐으면 "재실" 상태로 시작합니다. index.html의 "모르면 재실 쪽으로" 원칙과 같습니다.
- vision 카운트가 끊기면 결측 처리 표 그대로 동작합니다(30초 이내 직전 유지 → 5분까지 재실 유지 → 5분 초과 시 카메라 이상 알림 + 스케줄 기반 운전). 즉 **vision 장애는 control 안의 로직으로 흡수**됩니다.
- 그림자 모드(1~2주): 환경 변수 `CONTROL_MODE=shadow`로 명령을 발행하지 않고 판단만 기록합니다. 같은 이미지를 그대로 운영 모드로 전환할 수 있어, 검증한 코드와 배포하는 코드가 같습니다.

### 3-3. `occu-deviceio` — 하드웨어와의 경계

| 항목 | 내용 |
|---|---|
| 역할 | MCU와 UART 프로토콜(CRC16 + 시퀀스 번호, 1초 하트비트) 처리, 명령 전달·피드백 수신, BACnet/IP(UDP 47808) 연동, Modbus 전력량계 값 중계(MCU 경유) |
| 장치 | `/dev/ttymxc*` (MCU UART) |
| 네트워크 | BACnet/IP 현장만 사내 LAN 접근, 그 외 `none` |
| 자원 | 메모리 상한 약 64MB, 가능하면 C/Rust 또는 Go로 작성 |

**왜 control과 분리하는가**

- **하트비트의 주인**: MCU는 SoC 하트비트가 끊기면 SoC를 리셋하고 안전 모드로 들어갑니다. 하트비트를 무거운 control 프로세스가 보내면, control이 GC나 데이터 처리로 잠깐 멈출 때 불필요한 리셋이 생깁니다. 작고 단순한 deviceio가 보내되, **control·vision의 헬스 상태가 정상일 때만** 하트비트에 "정상" 플래그를 실어 보냅니다.
- **교체 빈도 차이**: 제어 로직(K1 구간, 파라미터)은 자주 바뀌지만 UART 프로토콜은 거의 바뀌지 않습니다. 자주 바뀌는 쪽과 안정적인 쪽을 나눠 업데이트 위험을 줄입니다.
- **권한 최소화**: 시리얼 장치와 사내망 접근 권한을 이 컨테이너 하나에만 줍니다.

### 3-4. `occu-uplink` — 밖으로 나가는 유일한 문

| 항목 | 내용 |
|---|---|
| 역할 | **L1 외부 전송 검사**(허용 목록 스키마: 숫자·상태·시각만, 이미지성 데이터·큰 페이로드 차단), **N2 오프라인 버퍼**(일련번호 + SQLite WAL, 24시간 이상), AWS IoT Core MQTT/TLS 연결, 클라우드 → 기기 설정·OTA 작업(IoT Jobs) 수신 |
| 네트워크 | 외부 **TCP 8883**(AWS IoT), 443(이미지 레지스트리, OTA 시)만 허용하는 egress 방화벽 |
| 비밀 정보 | X.509 장치 인증서·키 — 이 컨테이너에만 읽기 전용 마운트 (가능하면 i.MX CAAM/보안 요소에 키 보관, PKCS#11 사용) |
| 저장 | `/data/uplink` — 오프라인 버퍼 (24시간분 수 MB, 상한 256MB 순환) |
| 자원 | 메모리 상한 약 96MB |

**설계 포인트**

- 외부 네트워크가 있는 컨테이너가 이것 **하나뿐**이고, 이 컨테이너에는 카메라 장치가 없습니다. 브로커 ACL로 uplink는 `occu/vision/raw/#` 같은 토픽을 구독조차 할 수 없게 합니다. 영상이 나가려면 ① vision의 네트워크 격리, ② 브로커 ACL, ③ L1 스키마 검사 세 겹을 모두 뚫어야 합니다.
- 클라우드에서 내려오는 설정(파라미터 변경, L4 제안 승인)은 uplink가 받아 스키마 검사 후 브로커의 `occu/config/#`로만 넘깁니다. 클라우드가 기기 내부 컨테이너에 직접 닿는 경로는 없습니다.

### 3-5. `occu-broker` — 기기 내부 메시지 버스

| 항목 | 내용 |
|---|---|
| 구현 | Eclipse Mosquitto (메모리 약 5~10MB) |
| 연결 | 호스트의 `/run/occu/broker.sock` Unix 소켓 공유 (TCP 포트 없음) |
| 보안 | 컨테이너별 계정 + 토픽 ACL, 최대 페이로드 크기 제한(예: 4KB) |
| 저장 | 없음 (영속 메시지는 uplink 버퍼가 책임) |

**MQTT를 내부 버스로 쓰는 이유**: 클라우드 쪽이 이미 MQTT(AWS IoT Core)라 토픽 구조와 메시지 스키마를 기기 내부와 클라우드에서 똑같이 쓸 수 있습니다. 메시지 크기 제한이 영상 유출의 또 하나의 장벽이 됩니다. 프레임 단위 고속 전달이 필요한 구간은 vision 내부에 있으므로 MQTT 지연(수 ms)은 문제가 되지 않습니다.

### 3-6. `occu-insight` — 버려도 되는 분석

| 항목 | 내용 |
|---|---|
| 역할 | N1 알림 정규화·중복 억제(긴급/주의/정보 분류), L2 KPI 일일 산출, L3 절전 오작동 의심 구간 추출 |
| 출력 | `occu/insight/alert`, `occu/insight/kpi` → uplink가 전송 |
| 저장 | `/data/insight` — 일 단위 집계 |
| 자원 | 메모리 상한 약 128MB, **OOM 우선 종료 대상**, CPU 우선순위 최저 |

제어에 영향이 없는 기능을 따로 떼어, 메모리가 부족할 때 **제품이 먼저 포기할 대상**을 명확히 합니다. L4 월간 리포트와 E2 기준선 회귀(대시보드 전용)는 계산량이 크고 한 달치 데이터가 필요하므로 **클라우드에서** 수행합니다.

### 3-7. `occu-provision` — 설치 30분을 위한 도구

- BLE 5.4 페어링, Wi-Fi 설정, 장치 등록(인증서 발급 요청), 구역 ROI·Homography 보정을 맡습니다.
- **보정 중에만** 미리보기가 필요할 수 있으므로, 미리보기는 설치자 앱으로 BLE/로컬 Wi-Fi 직결 전송만 허용하고 저장하지 않습니다. 설치 모드는 물리 버튼으로만 진입하고, 30분 후 자동 종료합니다.
- 운영 중에는 실행되지 않아 상시 메모리를 쓰지 않습니다.

### 3-8. 컨테이너로 만들지 않는 것: MCU 펌웨어

압축기 3분 재기동 간격, 시간당 4회 제한, IR 38kHz 송신, RS-485 Modbus, SoC 워치독은 **MCU에 남깁니다**. control의 G1·P1은 "명령을 만들지 않는" 1차 보호이고, MCU는 "명령이 와도 실행하지 않는" 최종 보호입니다. SoC·컨테이너 전체가 멈춰도 에어컨 설비는 보호됩니다.

---

## 4. 호스트와 운영 기반

### 4-1. 호스트 OS와 컨테이너 런타임

| 항목 | 제안 | 이유 |
|---|---|---|
| OS | **Yocto 기반 최소 Linux** (NXP i.MX BSP) | NPU·ISP 드라이버가 BSP에 맞춰져 있음. 불필요한 패키지를 빼 공격면·메모리 축소 |
| rootfs | 읽기 전용 + A/B 파티션 | 정전·업데이트 실패에도 부팅 보장 |
| 런타임 | **Podman + systemd Quadlet** (운영) / Docker Compose (개발) | 데몬이 없어 상주 메모리가 적고, systemd의 재시작·워치독(`sd_notify`)·의존 순서를 그대로 사용. 이미지는 OCI 표준이라 개발용 Compose와 같은 이미지를 씀 |
| 호스트 OTA | **RAUC 또는 Mender** (A/B, 서명 번들) | index.html의 "보안 부트 · 서명 OTA" 충족, 실패 시 자동 롤백 |

> 팀이 Docker에 익숙하다면 운영에서도 Docker Engine + Compose로 시작해도 됩니다. 다만 dockerd·containerd 상주 메모리(수십~100MB)를 2GB 예산에 반영해야 합니다.

### 4-2. 자원 배분 (LPDDR4 2GB · A53 ×4)

| 대상 | CPU | 메모리 상한 | OOM 우선순위 |
|---|---|---|---|
| 호스트 OS + 런타임 | 코어 0 | ~300MB | 보호 |
| occu-vision | **코어 2, 3 전용** | 450MB | 보호 |
| occu-control | 코어 1 | 200MB | 보호 |
| occu-deviceio | 코어 0, 실시간 우선순위(SCHED_FIFO 낮은 값) | 64MB | 보호 |
| occu-broker | 코어 0 | 32MB | 보호 |
| occu-uplink | 코어 0, 1 | 96MB | 중간 |
| occu-insight | 코어 0, 1, nice 19 | 128MB | **가장 먼저 종료** |
| **합계** | | **약 1.27GB** | 여유 약 700MB (페이지 캐시·NPU 버퍼·피크) |

수치는 초기 예산이며, 실기기에서 24시간 연속 측정(index.html L3 · 24시간 발열 확인과 같은 시험)으로 조정합니다.

### 4-3. eMMC 파티션 (16GB 기준)

| 파티션 | 크기 | 내용 |
|---|---|---|
| boot A/B | 2 × 64MB | 서명된 커널·DTB |
| rootfs A/B | 2 × 1.5GB | 읽기 전용 호스트 OS |
| containers | 4GB | 컨테이너 이미지 현재 + 직전 버전 (롤백용) |
| models | 256MB | `/models/<버전>/` + `current` 심볼릭 링크 (모델 3MB × 여러 세대) |
| data | 2GB | `/data/control`, `/data/uplink`(오프라인 버퍼), `/data/insight`, 로그(순환) |
| 여유 | 나머지 | eMMC 마모 분산용으로 비워 둠 |

**모델을 이미지와 분리하는 이유**: 분기별 재학습(L3 → 재학습)마다 vision 코드까지 다시 배포할 필요가 없습니다. 모델 파일은 서명·해시를 검증한 뒤 `current` 링크만 바꾸고, vision을 재시작합니다. 문제가 생기면 링크를 이전 버전으로 돌립니다.

### 4-4. 쓰기 줄이기 (eMMC 수명)

- 로그는 `journald`를 휘발성(RAM) + 일정 크기만 영구 저장으로 설정하고, 컨테이너 로그는 크기 제한 순환.
- control 스냅샷은 상태가 바뀔 때만, uplink 버퍼는 SQLite WAL로 묶음 쓰기.
- vision은 디스크에 아예 쓰지 않습니다.

---

## 5. 내부 토픽 설계

| 토픽 | 발행 | 구독 | 내용 | 주기 |
|---|---|---|---|---|
| `occu/vision/zone/{id}/count` | vision | control | 인원수, 트랙 수, 품질 플래그, 캡처 시각 | 1초 (5fps 결과의 중앙값) |
| `occu/vision/health` | vision | control, insight | 밝기·선명도·정지·가림 점수, 추론 지연 p95 | 10초 |
| `occu/device/sensor` | deviceio | control | 온습도, 외기, 전력·CT | 10초 / 1분 |
| `occu/device/hvac/state` | deviceio | control, insight | 전원·온도·풍량·모드 피드백, 수동 조작 감지 | 변경 시 |
| `occu/control/command` | control | deviceio | 명령 ID + 목표 상태 | 이벤트 / 1분 |
| `occu/device/hvac/ack` | deviceio | control | 명령 ID별 적용 확인·실패 | 명령마다 |
| `occu/control/state` | control | uplink, insight | 재실 단계, 안전 모드, 보간 여부 | 10초 |
| `occu/insight/alert`, `.../kpi` | insight | uplink | 정리된 알림, 일일 KPI | 이벤트 / 1일 |
| `occu/config/#` | uplink | 각 컨테이너 | 클라우드에서 승인된 설정 | 이벤트 |
| `occu/sys/health/{name}` | 각 컨테이너 | deviceio | 생존 신호 | 5초 |

모든 메시지에 `schema_version`, `ts`(캡처/측정 시각, NTP 동기·엣지 기기 마스터 시계), `quality`(정상/보간/추정/무효)를 넣습니다. index.html의 품질 플래그 규칙과 같습니다.

---

## 6. 장애 시나리오와 동작

| 장애 | 감지 | 자동 동작 | 제품 영향 |
|---|---|---|---|
| vision 프로세스 종료 | systemd 재시작, control의 카운트 결측 | 30초~5분 재실 유지 → 5분 초과 시 카메라 이상 알림 + 스케줄 운전 | 에어컨이 꺼지지 않음 ("모르면 재실") |
| NPU 지연 증가(>200ms) | vision health의 p95 | 공실 시 0.5fps로 낮추고, 지속 시 알림 | 반응 속도 저하만 |
| control 종료 | systemd 재시작, 스냅샷 복원 | 재시작 동안 명령 없음 → 에어컨은 마지막 상태 유지 | 수십 초 판단 공백 |
| deviceio 또는 SoC 전체 정지 | MCU 하트비트 감시 | MCU가 안전 모드 진입, 필요 시 SoC 리셋 | 설비 보호 규칙은 계속 동작 |
| 브로커 종료 | systemd (다른 컨테이너가 의존) | 가장 먼저 재시작, 클라이언트 자동 재연결 | 수 초 |
| 네트워크·클라우드 단절 | uplink 연결 상태 | N2 버퍼에 저장, 복구 후 순서대로 재전송 | 제어 영향 없음, 로그 누락 0건 |
| 메모리 부족 | cgroup 압력 | insight 먼저 종료 → 나머지 유지 | 알림·KPI 지연만 |
| 업데이트 실패 | 헬스 게이트(5절 생존 신호 + 제어 성공률) | 이전 이미지·모델·rootfs로 자동 롤백 | 없음 |

**헬스체크 기준(예시)**: vision은 "최근 10초 내 추론 성공 + 캡처 시각 갱신", control은 "최근 1분 내 집계 완료", deviceio는 "MCU 응답 정상", uplink는 "버퍼 쓰기 가능". 컨테이너 단위 `HealthCmd` 또는 `sd_notify WATCHDOG=1`으로 구현합니다.

---

## 7. 보안 설정 (모든 컨테이너 공통)

- `--read-only` rootfs, 필요한 경로만 `tmpfs`/볼륨
- `--cap-drop=ALL`, 필요한 capability만 추가 (deviceio의 실시간 우선순위 정도)
- 기본 seccomp 프로필, `no-new-privileges`
- **`--privileged` 사용 금지** — 장치는 `--device`로 필요한 것만 전달
- 가능한 컨테이너는 rootless / 비루트 사용자로 실행
- 이미지 서명(cosign 등) 검증 후에만 실행, 레지스트리는 AWS ECR 비공개 저장소
- 디버그 셸·SSH는 운영 이미지에서 제외, 현장 진단은 클라우드에서 원격 명령(IoT Jobs)으로 제한

---

## 8. 업데이트(OTA) 흐름

```
클라우드에서 릴리스 승인 (이미지 태그 + 모델 버전 + 호환 BSP 버전 매트릭스)
  → AWS IoT Jobs로 단계적 배포 (파일럿 1대 → 10% → 전체)
  → uplink가 작업 수신 → 서명 검증 → 새 이미지/모델 다운로드 (기존 버전 유지)
  → 비업무 시간 또는 공실 상태에서만 교체
  → 헬스 게이트 10분 (생존 신호 + 명령 확인 성공률 + 추론 지연)
  → 통과: 이전 버전 정리 / 실패: 자동 롤백 + 클라우드 보고
```

| 업데이트 대상 | 방식 | 빈도(예상) |
|---|---|---|
| 모델 | `/models/<버전>` 교체 + vision 재시작 | 분기 1회 (재학습) |
| control 파라미터 | `occu/config` 메시지, 재시작 없음 | 월 1회 (L4 제안 승인) |
| 컨테이너 이미지 | 이미지 교체 | 필요 시 |
| 호스트 OS / BSP | RAUC/Mender A/B | 연 1~2회 (보안 패치) |

---

## 9. 저장소 구성 제안

```
edge/
├── containers/
│   ├── vision/        # Dockerfile, capture·infer·track·zone 코드
│   ├── control/       # 정제·상태 머신·제어 규칙, 그림자 모드
│   ├── deviceio/      # MCU UART 프로토콜, BACnet/IP
│   ├── uplink/        # L1 스키마, N2 버퍼, AWS IoT 클라이언트
│   ├── insight/       # N1·L2·L3
│   ├── broker/        # mosquitto.conf, acl
│   └── provision/     # BLE·보정 도구
├── schemas/           # 토픽별 JSON Schema (L1 허용 목록과 공유)
├── deploy/
│   ├── quadlet/       # 운영용 *.container 유닛
│   └── compose.dev.yml
└── models/            # 모델 매니페스트 (파일은 아티팩트 저장소)
```

개발용 Compose 예시(구조를 보여 주는 최소 형태):

```yaml
services:
  broker:
    image: occu/broker:1.0
    volumes: [ "/run/occu:/run/occu" ]
    network_mode: none
    restart: always

  vision:
    image: occu/vision:1.0
    devices: [ "/dev/video0", "/dev/media0", "/dev/galcore" ]
    volumes:
      - "/run/occu:/run/occu"
      - "/models/current:/models:ro"
    network_mode: none
    read_only: true
    tmpfs: [ "/tmp:size=64m" ]
    cpuset: "2,3"
    mem_limit: 450m
    cap_drop: [ ALL ]
    depends_on: [ broker ]
    restart: always

  control:
    image: occu/control:1.0
    environment: [ "CONTROL_MODE=shadow" ]   # 그림자 모드 → 운영 시 active
    volumes: [ "/run/occu:/run/occu", "/data/control:/data" ]
    network_mode: none
    read_only: true
    cpuset: "1"
    mem_limit: 200m
    cap_drop: [ ALL ]
    depends_on: [ broker ]
    restart: always

  deviceio:
    image: occu/deviceio:1.0
    devices: [ "/dev/ttymxc1" ]
    volumes: [ "/run/occu:/run/occu" ]
    network_mode: none
    mem_limit: 64m
    depends_on: [ broker ]
    restart: always

  uplink:
    image: occu/uplink:1.0
    volumes:
      - "/run/occu:/run/occu"
      - "/data/uplink:/data"
      - "/etc/occu/certs:/certs:ro"
    mem_limit: 96m
    cap_drop: [ ALL ]
    depends_on: [ broker ]
    restart: always

  insight:
    image: occu/insight:1.0
    volumes: [ "/run/occu:/run/occu", "/data/insight:/data" ]
    network_mode: none
    mem_limit: 128m
    oom_score_adj: 900
    depends_on: [ broker ]
    restart: unless-stopped
```

---

## 10. 다른 구조와 비교

| 구조 | 장점 | 단점 | 판단 |
|---|---|---|---|
| 단일 컨테이너(모놀리식) | 가장 가볍고 단순 | 영상·네트워크 권한이 한 프로세스에 공존 → 프라이버시를 구조로 보장 못 함. 한 부분 장애가 전체 장애 | 제외 |
| **기능 경계별 6+1개 (제안)** | 프라이버시·권한 분리, 장애 격리, 독립 업데이트, 메모리 예산 내 | 내부 버스·스키마 관리 필요 | **채택** |
| 단계별 마이크로서비스(15개 이상) | 단계별 교체 가능 | 2GB에서 메모리 부족, 프레임 전달 비용, 시각 정렬 복잡 | 제외 |
| 경량 K8s(k3s) | 대규모 오케스트레이션 | 상주 메모리 수백 MB, 단일 기기에선 이점 적음 | 제외 (클라우드 측에서만 고려) |

---

## 11. 하드웨어가 바뀌어도 같은 구조

Raspberry Pi 5 + Hailo-8L PoC(`report/inference_engine_report.md`)에서도 구조는 그대로 두고 **`occu-vision` 이미지만** 바꿉니다(ONNX → Hailo 컴파일러, `/dev/hailo0`). control·deviceio·uplink·insight는 다중 아키텍처(arm64) 이미지 하나로 두 플랫폼에서 같이 씁니다. 추론 엔진을 바꿔도 출력 토픽과 스키마는 같으므로 판단 로직을 다시 검증할 필요가 없습니다.

---

## 12. 도입 순서 (index.html 후처리 도입 순서와 맞춤)

| 단계 | 컨테이너 작업 |
|---|---|
| **1차 · 파일럿 설치 전** | broker · vision(D1·D2 포함) · control(C1·G1, 그림자 모드) · deviceio · uplink(L1·N2) 완성. 컨테이너별 자원 상한과 헬스체크 적용. 24시간 연속 실기기 시험 |
| **2차 · 그림자 모드 1~2주** | control의 K1·K2 판단을 로그로 검증, insight(N1·L2·L3) 추가, 비운영 시간 데이터로 vision D4 활성화 |
| **3차 · 파일럿 1개월 이후** | control을 `active`로 전환, OTA 단계적 배포·롤백 실전 점검, 클라우드 L4 리포트로 파라미터 조정 |

---

## 13. 추가로 확인할 항목

1. **NPU 컨테이너 패스스루 실측**: i.MX 8M Plus에서 `/dev/galcore` + VX 델리게이트를 컨테이너 안에서 썼을 때 호스트 직접 실행 대비 지연 차이 (실험 E0와 함께 측정)
2. **카메라 경로**: IMX462를 ISP 경로로 쓸지 ISI(raw) 경로로 쓸지에 따라 필요한 장치 파일과 노출·화이트밸런스 고정(V1) 방법이 달라짐
3. **실제 메모리 사용량**: Python 기반 control·insight가 상한 안에 드는지, 필요 시 control 핵심 루프만 C++/Rust로 이전
4. **보안 요소 사용 여부**: 장치 키를 CAAM/외부 보안 칩에 둘지 결정 (uplink 설계에 영향)
5. **런타임 최종 선택**: Podman Quadlet과 Docker Compose 중 팀 운영 역량에 맞는 쪽 확정

> 이 리포트의 메모리·CPU·파티션 수치는 index.html 사양에서 출발한 초기 추정치이며, 실기기 시험과 파일럿 로그로 조정해야 합니다.
