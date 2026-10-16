# OccuCool MCU · SBC 선정 리포트

> `index.html`에 정리된 OccuCool 제품 요구사항(How it works · 전처리 · 후처리 · AI 모델 · 추론 엔진 · 프라이버시 · 성능 지표 · Architecture · Hardware)을 기준으로, 메인 연산부(SBC/SoM)와 실시간 제어용 MCU 후보를 **전력 · 성능 · 무선 · 실시간 · 자료 · 생태계** 관점에서 비교하고 추천
>
> 작성일: 2026-10-16
> 참고 자료: `index.html`, `report/hardware_report.md`, `report/inference_engine_report.md`, `report/model_selection_report.md`, `report/sbc_container_report.md`, 제조사 공개 자료(문서 끝 출처)

---

## 0. 결론 요약

| 역할 | 추천 | 대안 | 한 줄 근거 |
|---|---|---|---|
| **양산 메인 SoC (SoM)** | **NXP i.MX 8M Plus** SoM | TI AM62A7 (저전력 대안), NXP i.MX 95 (차세대) | NPU 2.3 TOPS + ISP 2개 + 내장 Cortex-M7, 산업용 온도·15년 공급 프로그램, TFLite VX 델리게이트 경로가 index.html 배포 사양(TFLite INT8, ≤200ms)과 그대로 일치 |
| **PoC · 개발 보드** | **Raspberry Pi 5 + AI HAT+ (Hailo-8L)** | Pi 5 + AI HAT+ 2 (Hailo-10H) | 이미 저장소 PoC 구성이며, 자료·커뮤니티가 가장 많아 데이터 수집·그림자 모드 검증을 가장 빨리 시작 가능 |
| **서브 MCU (보드 내 안전 제어)** | **ST STM32G0B1** | ST STM32U5 (보안 강화판), NXP MCX A (단일 벤더) | UART 6개 이상·타이머·ADC가 IR/RS-485/SoC 링크/CT 측정에 충분, 저가·저전력, CubeMX/HAL·자료 풍부, 10년 공급 보증 |
| **원격 온습도 센서 노드 (내벽 1.2~1.5m)** | **Espressif ESP32-C6** 모듈 | Nordic nRF54L15 (BLE 전용 저전력) | index.html이 SHT4x를 "Wi-Fi 또는 RS-485 별도 모듈"로 둔다고 했으므로, Wi-Fi 6·인증 모듈·저가가 맞음 |
| **SoC 무선 모듈** | **NXP IW612 기반 모듈** (u-blox MAYA-W2, Murata Type 2EL 등) | 보드 내장 Wi-Fi (Pi 5, PoC 한정) | Wi-Fi 6 + BLE + 802.15.4, -40~85℃, i.MX BSP에 드라이버 포함 |

> **제외**: NVIDIA Jetson Orin Nano(전력 7~15W, 단가 과다, 이 모델에는 과잉 성능), Rockchip RK3576(성능·가격은 좋지만 문서 공개·장기 공급·보안 부트 자료에서 산업용 제품 기준에 못 미침 → 원가 절감판 후보로만 유지), i.MX 93(ISP 없음 + NPU 0.5 TOPS로 사선 설치 타일 추론에 여유 부족).

**최종 확정 조건**: i.MX 8M Plus 실기기 실험 **E0**에서 YOLO11n의 C2PSA 블록이 NPU에서 돌고 INT8 지연이 200ms 이하인지 확인합니다. 실패하면 ① YOLOv8n으로 모델을 바꾸거나 ② TI AM62A7로 SoC를 바꾸는 순서로 대응합니다 (6절).

---

## 1. index.html에서 뽑은 선정 기준

| 구분 | index.html 요구사항 | 선정 기준으로 바꾸면 |
|---|---|---|
| **성능** | YOLO11n(2.6M, ~2.7 GFLOPs@416) INT8, 5fps, 추론 ≤200ms, 원거리 타일링(V3·D5) | 실효 1~2 TOPS급 NPU면 충분. **TOPS보다 연산자 호환성(C2PSA)과 툴체인 성숙도가 중요** |
| **카메라** | IMX462 2MP, MIPI CSI-2, IR-Cut, 노출·WB 고정(V1), 야간 IR | **ISP 내장** + MIPI CSI-2, 가능하면 RGB-IR 처리 |
| **전력** | 평상시 ~5W, 최대 ~12W, 12V/2A 어댑터, PoE 옵션, 팬 없음 | SoC 부하 3W 이하가 이상적, 802.3af PoE(12.95W) 안에 들어가야 함 |
| **실시간** | 압축기 3분 보호, 시간당 4회 제한, IR 38kHz, Modbus RTU, SoC 워치독, 하트비트 1초 | **별도 MCU**(전원·리셋 분리), 하드웨어 타이머·입력 캡처·다중 UART |
| **무선** | BLE 5.4 페어링, Wi-Fi / PoE, MQTT over TLS | Wi-Fi + BLE 콤보, 산업용 온도, 전파 인증 모듈 |
| **신뢰성** | 가동률 99.5%, 24시간 오프라인 로컬 제어, 제어 성공률 99% | 산업용 온도 등급, 장기 공급(10년 이상), eMMC |
| **보안** | X.509, 보안 부트, 서명 OTA, 영상 외부 전송 0건 | HAB/보안 부트·암호 엔진·키 저장 지원, 공개 문서 |
| **메모리·저장** | LPDDR4 2GB, eMMC 16~32GB | SoM 옵션으로 제공되는지 |
| **소프트웨어** | TFLite INT8 + NPU 델리게이트, 컨테이너 구조(`sbc_container_report.md`) | Yocto/메인라인 리눅스, 컨테이너 런타임, NPU 런타임의 컨테이너 사용 가능 여부 |

---

## 2. 메인 SoC / SBC 후보 비교

### 2-1. 후보 사양

| 후보 | CPU | NPU | ISP | 내장 실시간 코어 | 메모리 | 비고 |
|---|---|---|---|---|---|---|
| **NXP i.MX 8M Plus** | Cortex-A53 ×4 (최대 1.8GHz) | 2.3 TOPS (VeriSilicon) | **2개** (HDR) | Cortex-M7 800MHz | LPDDR4 / DDR4 | 현재 index.html 기준 사양 |
| **NXP i.MX 95** | Cortex-A55 ×6 (최대 ~2GHz) | eIQ Neutron 2 TOPS | 있음 | Cortex-M7 + Cortex-M33 | LPDDR5 / LPDDR4X | 2026년 유통 본격화, SoM 다수 출시 |
| **NXP i.MX 93** | Cortex-A55 ×2 (1.7GHz) | Arm Ethos-U65 0.5 TOPS | **없음** | Cortex-M33 | LPDDR4X | 저전력·저가, 영상 AI에는 약함 |
| **TI AM62A7** | Cortex-A53 ×4 (1.4GHz) | 2 TOPS (C7x DSP + MMA) | **있음 (RGB-IR)** | Cortex-R5F | LPDDR4 / DDR4 | 1~2 카메라 저전력 비전 전용 칩 |
| **Rockchip RK3576** | A72 ×4 + A53 ×4 | 6 TOPS | 있음 | Cortex-M0 | LPDDR4X / LPDDR5 | 성능 대비 가격 최상 |
| **Rockchip RV1126B** | Cortex-A53 ×4 (1.6GHz) | 3 TOPS | 있음 (AI-ISP) | 없음 | 최대 4GB | IP 카메라 전용 칩, 2026년 SoM 출시 |
| **Raspberry Pi 5 + Hailo-8L** | Cortex-A76 ×4 (2.4GHz) | 13 TOPS (외장 PCIe) | 있음 (Pi ISP) | 없음 (RP1은 I/O 칩) | 4~8GB | PoC 구성 |
| **Raspberry Pi 5 + Hailo-10H** | 위와 같음 | 40 TOPS + 전용 8GB | 위와 같음 | 없음 | 위와 같음 | AI HAT+ 2, 약 $130 |
| NVIDIA Jetson Orin Nano | Cortex-A78AE ×6 | 최대 수십 TOPS (GPU) | 있음 | 없음 | 4~8GB | 참고용 상한 |

### 2-2. 항목별 비교

★5 = 매우 좋음, ★1 = 부적합 (OccuCool 요구사항 기준 상대 평가)

| 후보 | 전력 | 성능 (OccuCool 부하) | 무선 | 실시간 | 자료 | 생태계 · 공급 | 종합 |
|---|---|---|---|---|---|---|---|
| **i.MX 8M Plus** | ★★★★ 부하 약 2~4W | ★★★★ 2.3 TOPS면 5fps 충분. C2PSA 호환 확인 필요 | ★★★★ IW612 등 BSP 지원 모듈 | ★★★★ 내장 M7 (+외부 MCU) | ★★★★★ 참조 매뉴얼·데이터시트·eIQ 공개 | ★★★★★ SoM 업체 다수, 15년 공급 프로그램, Yocto | **추천** |
| **TI AM62A7** | ★★★★★ 부하 약 2W, 최대 3W 미만(85℃) | ★★★★ 2 TOPS, TIDL은 YOLO 계열 지원 목록 있음 | ★★★ 외장 모듈 필요, TI 무선 칩 조합 | ★★★★★ R5F + 기능 안전 지향 | ★★★★★ TRM·Edge AI SDK·모델 존 공개 | ★★★★ SoM 수가 NXP보다 적음, 장기 공급 | **1순위 대안** |
| **i.MX 95** | ★★★ i.MX 8M Plus보다 고성능·고전력 | ★★★★ 2 TOPS, Neutron 툴체인 신규라 연산자 확인 필요 | ★★★★ IW612 계열 | ★★★★★ M7 + M33, 안전 설계 | ★★★★ 문서 공개, 사례는 아직 적음 | ★★★★ SoM 다수, 단가 높음 | 차세대(v2) |
| **i.MX 93** | ★★★★★ 매우 낮음 | ★★ 0.5 TOPS, 타일 추론 여유 부족 | ★★★★ IW612 계열 | ★★★★ M33 | ★★★★★ | ★★★★★ | 부적합 (ISP 없음) |
| **RK3576** | ★★★ 부하 약 3~6W | ★★★★★ 6 TOPS, RKNN에서 YOLO11 예제 제공 | ★★★ 보드마다 다름 | ★★ M0만 | ★★ TRM 공개 제한적, 중국어 자료 비중 큼 | ★★★ SBC 많음, 장기 공급·보안 부트 자료 부족 | 원가 절감판 후보 |
| **RV1126B** | ★★★★★ 카메라용 저전력 | ★★★★ 3 TOPS | ★★ 외장 모듈 | ★ 없음 | ★★ 신규 칩, 자료 적음 | ★★ 2026년 SoM 등장 단계 | 관찰 대상 |
| **Pi 5 + Hailo-8L** | ★★ 보드 약 3W 대기, 부하 8~12W | ★★★★★ 13 TOPS, Hailo 모델 존에 YOLO 다수 | ★★★★ Wi-Fi 5·BT 5.0 내장 인증 | ★ 없음 (외부 MCU 필수) | ★★★★★ 자료·커뮤니티 최다 | ★★★ 소비자용, 산업 온도 아님, CM5로 제품화 가능 | **PoC 추천** |
| **Pi 5 + Hailo-10H** | ★★ 위보다 높음 | ★★★★★ 과잉 (LLM용) | ★★★★ | ★ | ★★★★ | ★★★ | PoC에도 과잉 |
| Jetson Orin Nano | ★ 7~15W, 팬 필요 | ★★★★★ 과잉 | ★★★ | ★★ | ★★★★★ | ★★★★ 단가 높음 | 제외 |

### 2-3. 항목별 해설

**전력** — index.html의 평상시 ~5W, 최대 ~12W 예산은 SoC 부하가 3W 안쪽일 때 여유가 생깁니다. 카메라(약 0.5W), Wi-Fi(송신 피크), IR 송신 펄스까지 더해지기 때문입니다. AM62A7(약 2W)과 i.MX 8M Plus(약 2~4W)는 무팬 밀폐 케이스와 802.3af PoE 안에 들어가지만, Pi 5 + Hailo 조합은 부하 시 PoE 예산을 넘을 수 있어 제품용으로는 맞지 않습니다.

**성능** — YOLO11n@416은 프레임당 약 2.7 GFLOPs로, 5fps면 초당 약 14 GFLOPs입니다. 2 TOPS급 NPU의 실효 성능(이론값의 10~30%)으로도 충분합니다. 따라서 **TOPS 수치보다 다음이 결정적**입니다.

| 확인 항목 | i.MX 8M Plus | AM62A7 | RK3576 | Hailo |
|---|---|---|---|---|
| 런타임 | TFLite + VX 델리게이트 | TFLite/ONNX + TIDL | RKNN | HailoRT |
| 변환 툴체인 | eIQ (TFLite INT8 그대로) | TIDL 컴파일러 | RKNN-Toolkit2 | Hailo DFC |
| YOLO11 C2PSA(어텐션) | **불확실 → E0 필수** | 지원 목록 확인 필요 | 예제 제공 | 모델 존 제공 |
| index.html 배포 사양(TFLite INT8)과 일치 | **그대로 일치** | 부분 일치 (TIDL 변환 추가) | 재변환 필요 | 재변환 필요 |

**무선** — 어떤 SoC도 Wi-Fi/BLE를 칩에 내장하지 않으므로 외장 모듈이 필요합니다. NXP IW612는 Wi-Fi 6 + BLE + 802.15.4 3중 무선이고 i.MX BSP에 드라이버가 포함돼 있습니다. **주의: IW612 모듈(u-blox MAYA-W2 등)은 BLE 5.2로 표기됩니다.** index.html의 "BLE 5.4"는 설치용 페어링에만 쓰이므로 5.2로도 기능상 충분하지만, 문구를 맞추려면 BLE 5.4 인증 모듈을 고르거나 index.html 문구를 "BLE 5.x"로 수정해야 합니다.

**실시간** — 압축기 보호·하트비트 감시는 SoC가 리셋돼도 살아 있어야 합니다. i.MX 8M Plus의 내장 M7, AM62A7의 R5F도 쓸 수 있지만 **같은 칩·같은 전원**이라 SoC 리셋·전원 문제에 함께 영향받습니다. `hardware_report.md`와 같이 **외부 MCU를 기본**으로 두고, 내장 코어는 v2 원가 절감 때 검토합니다.

**자료** — 맞춤 기판(캐리어 보드)을 설계하려면 참조 매뉴얼(TRM), 하드웨어 설계 가이드, 전원 시퀀스 문서가 공개돼 있어야 합니다. NXP·TI는 모두 공개, Rockchip은 일부만 공개되거나 보드 업체를 통해 받아야 합니다.

**생태계 · 공급** — 제품 수명(설치 후 5~10년 유지보수)을 고려하면 장기 공급 보증이 중요합니다. NXP i.MX 8M Plus는 15년 공급 프로그램 대상이고 Variscite·Toradex·CompuLab·SolidRun·Advantech 등 SoM 선택지가 가장 많습니다. Pi 5는 소비자 보드라 산업용 온도·장기 공급 조건이 약하고, 제품화하려면 Compute Module 5로 바꿔야 합니다.

---

## 3. MCU 후보 비교

### 3-1. MCU가 맡는 일 (index.html Hardware 섹션)

| 기능 | 필요한 주변장치 |
|---|---|
| SoC 링크 (CRC16 + 시퀀스, 115,200~921,600bps, 하트비트 1초) | UART ×1 (DMA) |
| RS-485 Modbus RTU (시스템 에어컨, 전력량계, 외기 센서) | UART ×1~2 + DE/RE GPIO |
| IR 송신 38kHz, 듀티 1/3 | 하드웨어 타이머 PWM |
| IR 수신 (리모컨 학습, 수동 조작 감지) | 타이머 입력 캡처 |
| SHT4x·SCD41·RTC (I²C 0x44, 0x62, 0x52) | I²C ×1 |
| CT 간이 측정 (옵션 C) | 12비트 ADC |
| 압축기 3분 보호, 시간당 4회 제한, 안전 모드 | 독립 워치독, 저전력 타이머 |
| SoC 리셋(POR_B) 제어, LED·버튼·부저 | GPIO |
| 디버그 로그 | UART ×1 |

→ **UART 4개 이상, 32비트 타이머, 입력 캡처, ADC, 독립 워치독**이 핵심입니다. 연산 성능은 거의 필요 없습니다.

### 3-2. 보드 내 서브 MCU 후보

| 후보 | 코어 | UART | 전력 | 실시간 | 자료 | 생태계 · 공급 | 판단 |
|---|---|---|---|---|---|---|---|
| **ST STM32G0B1** | M0+ 64MHz, 플래시 최대 512KB | USART 6 + LPUART 2 | ★★★★ 약 100µA/MHz | ★★★★★ 타이머·입력 캡처 풍부, IWDG | ★★★★★ CubeMX·HAL·예제 최다 | ★★★★★ 10년 공급 보증, 저가(수 달러), 125℃ 등급 | **추천** |
| ST STM32U5 | M33 160MHz, TrustZone | 다수 | ★★★★★ 19.5µA/MHz, 대기 300nA | ★★★★★ | ★★★★★ | ★★★★★ 단가 G0의 2~3배 | 보안 강화판 (MCU 펌웨어 서명·키 보관까지 원할 때) |
| ST STM32L4 | M4 80MHz | 다수 | ★★★★★ | ★★★★★ | ★★★★★ | ★★★★★ | `hardware_report.md` 후보, U5로 대체되는 추세 |
| NXP MCX A153 | M33 96MHz | LPUART 3 | ★★★★ | ★★★★ | ★★★★ | ★★★★ 신규 제품군, SoC와 같은 벤더 | 단일 벤더 지원을 원할 때 |
| Raspberry Pi RP2350 | M33 ×2 150MHz | UART 2 (+PIO) | ★★★ | ★★★★ PIO로 IR 구현 쉬움 | ★★★★★ | ★★★ 외장 플래시 필요, UART 부족 | PoC 보조용 |
| Espressif ESP32-C6 | RISC-V 160MHz + LP 코어 | UART 2~3 | ★★ Wi-Fi 사용 시 높음 | ★★ Wi-Fi 스택과 공존 시 지연 편차 | ★★★★ | ★★★★ | 안전 MCU로는 부적합 (센서 노드용) |
| i.MX 8M Plus 내장 M7 | M7 800MHz | SoC와 공유 | — | ★★★ SoC와 리셋·전원 공유 | ★★★★ | — | v2 원가 절감 검토 |

**STM32G0B1을 고른 이유**: 필요한 UART 수(SoC 링크, RS-485 ×1~2, 디버그)를 여유 있게 채우는 가장 저렴한 계열이고, 3.3V 단일 전원 약 6~10mA로 index.html 수치와 맞습니다. 펌웨어가 하는 일이 "보호 규칙 + 프로토콜 중계"로 단순해서, 높은 성능보다 **예측 가능한 동작과 풍부한 예제**가 더 중요합니다.

### 3-3. 원격 온습도 센서 노드 MCU

index.html은 SHT4x를 "토출 기류·본체 발열을 피해 내벽 1.2~1.5m 별도 모듈(Wi-Fi 또는 RS-485)"로 둔다고 합니다. 이 노드는 본체 MCU와 요구사항이 다릅니다.

| 후보 | 무선 | 전력 | 생태계 | 판단 |
|---|---|---|---|---|
| **ESP32-C6** 모듈 | Wi-Fi 6 (2.4GHz) + BLE 5 + 802.15.4 | 딥슬립 약 1µA급, Wi-Fi 송신 피크 수백 mA | ESP-IDF, 전파 인증 모듈 저가, 자료 최다 | **추천** — 본체와 같은 Wi-Fi로 붙어 설치 단순 |
| Nordic nRF54L15 | BLE 전용 (+Thread) | 딥슬립 1µA 미만, 가장 낮음 | nRF Connect SDK (Zephyr) | 배터리 구동이 필요할 때. 본체가 BLE로 수신해야 함 |
| STM32G0 + RS-485 | 유선 | 낮음 | ST 생태계 | 상업 현장(배선 가능, 무선 간섭 우려)용 |

Wi-Fi 노드는 S3(결측 계층 처리)·품질 플래그를 전제로 설계돼 있으므로, 짧은 통신 누락은 index.html 전처리 규칙이 흡수합니다.

---

## 4. 추천 조합

### 4-1. 양산 (파일럿 설치용 맞춤 캐리어 보드)

| 블록 | 부품 | 전력 (평상시) |
|---|---|---|
| 메인 SoM | **i.MX 8M Plus SoM** (LPDDR4 2GB, eMMC 16~32GB, 산업용 온도) | 약 3W |
| 서브 MCU | **STM32G0B1** (별도 3.3V LDO, POR_B 리셋 제어) | 약 0.03W |
| 무선 | **IW612 기반 모듈** (Wi-Fi 6 + BLE + 802.15.4) | 평균 약 0.3~0.5W |
| 원격 센서 노드 | **ESP32-C6 모듈 + SHT4x** (별도 전원) | 본체 예산 외 |
| 보안 | SoC HAB 보안 부트 + 보안 소자(SE050 등, `hardware_report.md`) | 미미 |
| **합계** | | **약 5W 이내 → index.html 평상시 ~5W와 일치** |

### 4-2. PoC (지금 바로 시작)

| 블록 | 부품 | 비고 |
|---|---|---|
| 메인 보드 | Raspberry Pi 5 (4GB) + AI HAT+ (Hailo-8L, 13 TOPS) | 현재 저장소 PoC, 데이터 수집·그림자 모드 |
| 서브 MCU | **STM32G0B1 Nucleo 보드** | 양산과 **같은 MCU 펌웨어**를 미리 검증 |
| 센서 노드 | ESP32-C6 개발 보드 + SHT4x | 양산과 동일 |

PoC에서도 MCU·센서 노드를 양산과 같은 칩으로 맞추면, 메인 SoC만 바꿔 넘어갈 수 있습니다. `sbc_container_report.md` 11절대로 컨테이너 구조도 `occu-vision` 이미지만 교체하면 됩니다.

---

## 5. 대안 시나리오

| 상황 | 대응 |
|---|---|
| E0에서 C2PSA가 i.MX 8M Plus NPU에서 CPU로 넘어감 | ① YOLOv8n으로 교체 (index.html에 명시된 대체 경로) → ② 그래도 지연 초과면 AM62A7 평가 |
| 무팬·PoE 전력 예산이 빠듯함 | **TI AM62A7** (부하 약 2W, RGB-IR ISP가 IMX462 야간 IR과 잘 맞음) |
| 1대로 여러 카메라·넓은 공간(300㎡ 이상) | i.MX 95 또는 RK3576 (여유 성능) |
| 원가 절감판(v2) | MCU를 SoC 내장 코어로 통합하거나, RK3576/RV1126B를 문서·공급 조건 확인 후 재평가 |
| 프라이버시 민감 현장 (ToF·열화상) | 영상 NPU가 필요 없으므로 **MCU 단독 구성** (STM32U5 + MLX90640/VL53L5CX) 별도 제품 라인 검토 |

---

## 6. 검증 계획 (구매 전후 확인할 것)

| # | 실험 | 합격 기준 (index.html) | 대상 |
|---|---|---|---|
| H1 | YOLO11n / YOLOv8n INT8을 NPU에서 실행 (E0) | ≤200ms, 5fps 유지, CPU 대체 연산 없음 | i.MX 8M Plus (+ 필요 시 AM62A7) |
| H2 | 24시간 연속 추론 + 밀폐 케이스 온도 | 열 스로틀링 없음, 평상시 ~5W | 메인 SoM |
| H3 | PoE 802.3af 최악 조건 (IR 조명 + Wi-Fi 송신 + 추론) | 12.95W 이내 | 전체 보드 |
| H4 | SoC 강제 정지 → MCU 안전 모드 | 하트비트 끊김 감지, 압축기 보호 유지, SoC 리셋 | STM32G0B1 |
| H5 | IR 송신·수신 (38kHz, 학습·수동 조작 감지) | 대상 에어컨 명령 성공률 99% | STM32G0B1 |
| H6 | 센서 노드 Wi-Fi 누락률 | S3 결측 처리 범위(30초~5분) 내 | ESP32-C6 |
| H7 | 컨테이너 안 NPU 사용 | 호스트 직접 실행 대비 지연 차이 미미 | 메인 SoM |

---

## 7. 추가로 확인할 항목

1. **단가**: SoM·모듈 가격은 수량·메모리 가격에 따라 크게 달라 이 리포트에 넣지 않았습니다. SoM 업체 2~3곳에 파일럿 수량 견적을 받아야 합니다.
2. **BLE 버전 문구**: IW612 모듈은 BLE 5.2입니다. index.html "BLE 5.4"를 유지할지 결정이 필요합니다.
3. **RGB-IR 처리**: IMX462를 i.MX 8M Plus ISP로 쓸 때 IR-Cut 전환과 노출 고정(V1)이 드라이버에서 지원되는지 SoM 업체 카메라 지원 목록으로 확인합니다.
4. **전파 인증**: 국내 KC 인증을 받은 무선 모듈인지 확인합니다 (모듈 인증 활용 시 제품 인증 부담 감소).
5. 이 리포트의 전력·성능 수치는 제조사 공개 자료와 일반적인 사용 사례 기준의 추정치이며, 실기기 측정(6절)으로 확정해야 합니다.

---

## 출처

- NXP i.MX 95 SoM 출시 현황: [CNX Software – Geniatech i.MX 95 SoM](https://www.cnx-software.com/2026/01/15/geniatech-smarc-and-osm-system-on-modules-nxp-i-mx-95-soc/), [LinuxGizmos – FRDM i.MX 95 Pro](https://linuxgizmos.com/nxp-frdm-i-mx-95-pro-pairs-six-cortex-a55-cores-with-8-etops-ai-acceleration/), [Electronic Design – Mouser i.MX 95](https://www.electronicdesign.com/directory/automation/iiot/press-release/55391356/mouser-electronics-nxp-semiconductors-imx-95-applications-processors-available-from-mouser-enable-industrial-iot-automotive-aerospace-and-medical-applications)
- TI AM62A7: [TI 제품 페이지](https://www.ti.com/product/AM62A7), [TI 응용 노트 SPRADB8](https://ti.com/document-viewer/lit/html/SPRADB8/GUID-1CBD2C3C-FE95-4D63-BE77-4FCF33BEEA59)
- Rockchip RK3576: [BLIIoT – RK3576 Deep Dive](https://bliiot.com/info-detail/rk3576-deep-dive-6-tops-npu-meets-unbeatable-cost-performance-in-aiot), [CNX Software – Firefly ROC-RK3576-PC](https://www.cnx-software.com/2024/07/30/firefly-roc-rk3576-pc-low-profile-rockchip-rk3576-sbc-supports-ai-models-like-gemma-2b-llama2-7b-chatglm3-6b/)
- Rockchip RV1126B: [Electronics-Lab – Graperain RV1126B SoM](https://www.electronics-lab.com/graperain-rv1126b-som-packs-a-rockchip-ai-vision-soc-and-3-tops-npu/), [CNX Software – GR1126MB](https://www.cnx-software.com/2026/06/24/graperain-gr1126mb-development-board-features-solder-on-3-tops-rockchip-rv1126b-ai-vision-module/?amp=1)
- Raspberry Pi AI HAT+ 2 (Hailo-10H): [heise](https://heise.de/-11136612), [raspberry.tips](https://raspberry.tips/en/?p=9674)
- 저전력 MCU 비교: [Promwad – Best MCUs for Low-Power IoT](https://promwad.com/news/best-microcontrollers-low-power-iot-2025), [DigiKey – STM32U5](https://www.digikey.com/en/product-highlight/s/stmicroelectronics/stm32u5-series-ultra-low-power-mcus)
- NXP IW612 / u-blox MAYA-W2: [CNX Software – MAYA-W2](https://cnx-software.com/2022/05/13/u-blox-maya-w2-iot-module-wifi-6-bluetooth-le-5-2-zigbee-thread/?amp=1), [DigiKey – MAYA-W2](https://www.digikey.com/en/product-highlight/u/u-blox/maya-w2-series-modules)
