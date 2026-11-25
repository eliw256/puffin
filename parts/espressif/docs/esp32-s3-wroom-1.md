# ESP32-S3-WROOM-1 / WROOM-1U pin reference

Extracted from the ESP32-S3-WROOM-1 & WROOM-1U Datasheet v1.8, Table 3-1.

## Pins

| No. | Name | Type | Functions |
|---|---|---|---|
| 1 | GND | P | Power |
| 2 | 3V3 | P | Power |
| 3 | EN | I | Chip enable; do not leave floating |
| 4 | IO4 | I/O/T | RTC_GPIO4, GPIO4, TOUCH4, ADC1_CH3 |
| 5 | IO5 | I/O/T | RTC_GPIO5, GPIO5, TOUCH5, ADC1_CH4 |
| 6 | IO6 | I/O/T | RTC_GPIO6, GPIO6, TOUCH6, ADC1_CH5 |
| 7 | IO7 | I/O/T | RTC_GPIO7, GPIO7, TOUCH7, ADC1_CH6 |
| 8 | IO15 | I/O/T | RTC_GPIO15, GPIO15, U0RTS, ADC2_CH4, XTAL_32K_P |
| 9 | IO16 | I/O/T | RTC_GPIO16, GPIO16, U0CTS, ADC2_CH5, XTAL_32K_N |
| 10 | IO17 | I/O/T | RTC_GPIO17, GPIO17, U1TXD, ADC2_CH6 |
| 11 | IO18 | I/O/T | RTC_GPIO18, GPIO18, U1RXD, ADC2_CH7, CLK_OUT3 |
| 12 | IO8 | I/O/T | RTC_GPIO8, GPIO8, TOUCH8, ADC1_CH7, SUBSPICS1 |
| 13 | IO19 | I/O/T | RTC_GPIO19, GPIO19, U1RTS, ADC2_CH8, CLK_OUT2, USB_D- |
| 14 | IO20 | I/O/T | RTC_GPIO20, GPIO20, U1CTS, ADC2_CH9, CLK_OUT1, USB_D+ |
| 15 | IO3 | I/O/T | RTC_GPIO3, GPIO3, TOUCH3, ADC1_CH2 |
| 16 | IO46 | I/O/T | GPIO46 |
| 17 | IO9 | I/O/T | RTC_GPIO9, GPIO9, TOUCH9, ADC1_CH8, FSPIHD, SUBSPIHD |
| 18 | IO10 | I/O/T | RTC_GPIO10, GPIO10, TOUCH10, ADC1_CH9, FSPICS0, FSPIIO4, SUBSPICS0 |
| 19 | IO11 | I/O/T | RTC_GPIO11, GPIO11, TOUCH11, ADC2_CH0, FSPID, FSPIIO5, SUBSPID |
| 20 | IO12 | I/O/T | RTC_GPIO12, GPIO12, TOUCH12, ADC2_CH1, FSPICLK, FSPIIO6, SUBSPICLK |
| 21 | IO13 | I/O/T | RTC_GPIO13, GPIO13, TOUCH13, ADC2_CH2, FSPIQ, FSPIIO7, SUBSPIQ |
| 22 | IO14 | I/O/T | RTC_GPIO14, GPIO14, TOUCH14, ADC2_CH3, FSPIWP, FSPIDQS, SUBSPIWP |
| 23 | IO21 | I/O/T | RTC_GPIO21, GPIO21 |
| 24 | IO47 | I/O/T | SPICLK_P_DIFF,GPIO47, SUBSPICLK_P_DIFF |
| 25 | IO48 | I/O/T | SPICLK_N_DIFF,GPIO48, SUBSPICLK_N_DIFF |
| 26 | IO45 | I/O/T | GPIO45 |
| 27 | IO0 | I/O/T | RTC_GPIO0, GPIO0 |
| 28 | IO35 | I/O/T | SPIIO6, GPIO35, FSPID, SUBSPID |
| 29 | IO36 | I/O/T | SPIIO7, GPIO36, FSPICLK, SUBSPICLK |
| 30 | IO37 | I/O/T | SPIDQS, GPIO37, FSPIQ, SUBSPIQ |
| 31 | IO38 | I/O/T | GPIO38, FSPIWP, SUBSPIWP |
| 32 | IO39 | I/O/T | MTCK, GPIO39, CLK_OUT3, SUBSPICS1 |
| 33 | IO40 | I/O/T | MTDO, GPIO40, CLK_OUT2 |
| 34 | IO41 | I/O/T | MTDI, GPIO41, CLK_OUT1 |
| 35 | IO42 | I/O/T | MTMS, GPIO42 |
| 36 | RXD0 | I/O/T | U0RXD, GPIO44, CLK_OUT2 |
| 37 | TXD0 | I/O/T | U0TXD, GPIO43, CLK_OUT1 |
| 38 | IO2 | I/O/T | RTC_GPIO2, GPIO2, TOUCH2, ADC1_CH1 |
| 39 | IO1 | I/O/T | RTC_GPIO1, GPIO1, TOUCH1, ADC1_CH0 |
| 40 | GND | P | Power |
| 41 | EPAD | P | Power |

## Strapping pins (Table 4-1)

| Pin | Default | Bit | Controls |
|---|---|---|---|
| GPIO0 | Weak pull-up | 1 | Boot mode |
| GPIO3 | Floating | none | JTAG signal source |
| GPIO45 | Weak pull-down | 0 | VDD_SPI voltage |
| GPIO46 | Weak pull-down | 0 | Boot mode, ROM message printing |

## Variant restrictions (Table 3-1 notes)

- Octal SPI PSRAM variants (R8, R16V): GPIO35, GPIO36, and GPIO37 are unavailable.
- R16V variants: GPIO47 and GPIO48 operate at 1.8 V.

## Supply (Table 6-2)

VDD33: 3.0 V min, 3.3 V typical, 3.6 V max.

Rails in the part file: GND (pins 1, 40, 41) is `gnd`; 3V3 (pin 2) is `3v3`.
