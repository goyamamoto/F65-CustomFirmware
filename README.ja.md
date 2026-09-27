# EPOMAKER x AULA F65（V1）カスタムファームウェア

[English](README.md)

EPOMAKER x AULA F65 の最初のモデル（"V1"、"Fn-Ctrl" 版のファームウェアが入っているもの。MCU は BYK916 = SinoWealth SH68F90 系）向けのオープンソースファームウェアです。この 8051 系 MCU では QMK も ZMK も動かないので、Karolis Stasaitis 氏の SinoWealth 8051 用キーボードファームウェア [smk](https://github.com/carlossless/smk) への移植として作りました。このリポジトリは smk に F65 を足したもの（と、その土台にした作者の NuPhy Air75 移植）で、smk が対応する他の機種もそのままビルドできます。

> [!WARNING]
> メーカーのファームウェアを置き換える、実験的なものです。純正に戻すには自分の基板のバックアップ（手順 3）が必要で、メーカーのファームウェアはここでは配っていません。ブートローダに戻れないファームウェアを書いてしまうと、ハードウェアの書き込み器（例: [sinodude-serial](https://github.com/carlossless/sinodude) を載せた Arduino Nano）でしか復旧できません。この移植には起動時の逃げ道（ブートエスケープ）を入れてありますが、自己責任で使ってください。

> [!CAUTION]
> **V1 専用です。** F65 V2（"Alt-Fn" 版、USB の product が "AULA F65"、VIA 対応）はキーの列が別のピンにつながっていて、このイメージを書くとキーが効かないキーボードになります。手順 1 で USB の ID と product 名を必ず確かめてください。F65 Pro は別のキーボードです。

## できること

- 67 キー全部、Windows と Mac のレイヤー（Fn+A / Fn+S、保存される）、設定は抜き差ししても残る
- USB、Bluetooth（3 スロット、名前は "AULA F65 BT5.0"）、2.4 GHz ドングル。純正の無線モジュールとプロトコルをそのまま使う
- 純正のバックライト効果（14 種類）、色、明るさ、速さ、サイドライト。状態の表示灯（Caps Lock、Fn を押している間のリンクのキー、ペアリング、Fn+B の電池残量、電池残量低下、充電）はバックライトの上に重ねて出る
- 電池では約 1 分操作がないとスリープ（キーで起きる）、30 秒でバックライトが消える、純正と同じ電池残量低下時の停止
- **US-JIS**（Fn+Tab、`usjis` レイアウト）: ホストが日本語キーボード配列でも、US キーキャップの印字どおりに打てる（Windows レイヤー）
- **IME キー**（Space の両側、`usjis` レイアウト）: 短く押すと英数 / かな（Mac）または無変換 / 変換（Windows）、押したままだと Command / Alt と右 Ctrl
- **ブートエスケープ**: Esc を押したまま電源を入れると、このファームウェアの USB のコードが動く前にブートローダで立ち上がる
- 有線では純正の USB ID のままなので、`sinowisp` は `aula-f75` として見つける

## レイアウトを選ぶ

ハードウェアの対応、ブートエスケープ、Bluetooth の名前、スリープはどのイメージも同じです。それ以外に欲しいものでレイアウトを選んでください。

| レイアウト | 追加されるもの | イメージ |
| --- | --- | --- |
| `ansi` | なし。印字どおりの US ANSI | `aula-f65-v1_ansi_smk.hex` |
| `usjis` | US-JIS（Fn+Tab）、Space の両側の IME キー（Fn と右 Ctrl の位置が入れ替わる） | `aula-f65-v1_usjis_smk.hex` |

## 対応キーボード

| キーボード | USB ID | 状態 |
| --- | --- | --- |
| EPOMAKER x AULA F65 最初のモデル（V1、"Fn-Ctrl" 版ファームウェア）、ANSI | 258a:010c、manufacturer "BY Tech"、product "Gaming Keyboard" | 動作。macOS ホストで USB・Bluetooth・2.4 GHz ドングルを実機で確認 |

非対応: F65 V2（同じ 258a:010c だが product "AULA F65"、bcdDevice 0x1005）と F65 Pro。

## 手順

1. **キーボードを確かめる。** 背面のスイッチを真ん中（USB）にして差します。USB ID `258a:010c`、manufacturer "BY Tech"、product "Gaming Keyboard" で見えなければなりません（macOS: システム情報 > USB）。product が "AULA F65" なら V2 なので、ここでやめてください。
2. **ツールを入れる。** [sinowisp](https://github.com/carlossless/sinowisp) を `cargo install sinowisp` で入れます（[Rust](https://rustup.rs/) が必要。sinowisp は純正のブートローダを通して USB でフラッシュを読み書きします）。macOS では、入力監視の権限があるターミナルアプリから実行してください（システム設定 > プライバシーとセキュリティ > 入力監視）。ないとキーボードを開けません。USB ID が 258a:010c の他のキーボードは外しておきます（多くの機種が同じ ID で、sinowisp は最後に見つけたものを使います）。
3. **純正ファームウェアをバックアップする。** ファイルは安全な所に保管します。純正に戻す唯一の手段です。
   ```sh
   sinowisp read -d aula-f75 f65-stock.hex                 # ファームウェア。戻すときに使う（手順 8）
   sinowisp read -d aula-f75 -s full f65-stock-full.hex    # ファームウェアとブートローダ。保存用
   ```
   ファームウェアは 2 回読んで比べてください。読み出しが安定していることを確かめてから書きます。
4. **ファームウェアを用意する。** レイアウトを選び（上）、[firmware/aula-f65-v1](firmware/aula-f65-v1) のイメージ（`shasum -a 256 -c SHA256SUMS` で確認）を使うか、自分でビルドします（[ビルド](#ビルド)）。
5. **書き込む。** スイッチは真ん中、USB でつないだ状態で。
   ```sh
   sinowisp write -d aula-f75 --force aula-f65-v1_usjis_smk.hex    # または aula-f65-v1_ansi_smk.hex
   ```
   イメージがフラッシュより小さいので `--force` が要ります。sinowisp は残りを 0 で埋めるため、設定も初期値に戻ります（書くたびに、使うなら Fn+Tab で US-JIS、Fn+S で Mac レイヤーを設定し直します）。
6. **何より先に、戻り道を確かめる。**
   - 新しいファームウェアが動いている状態で `sinowisp read -d aula-f75 check.hex` ができること。
   - USB を抜き（スイッチが真ん中なら電源が切れます）、数秒待ち、Esc を押したまま差して、2 秒ほどで Esc を離す: `0603:1020`（"SINO WEALTH" / "Gaming KB"）、つまりブートローダとして見えること。Esc を押さずに抜き差しするとファームウェアに戻ります。

   将来のビルドで USB が壊れても、2 つ目の方法でブートローダに入れます。無線の位置からは、先にスイッチを真ん中にしてください（そうしないと電池で動き続けます）。
7. **使う。** [キーマップ](#キーマップ)を見てください。ドングルは 2.4 GHz の位置で Fn+R を 3 秒、Bluetooth のスロットは Bluetooth の位置で Fn+Q / W / E を 3 秒押してペアリングします。
8. **純正に戻す**（いつでも。スイッチは真ん中）:
   ```sh
   sinowisp write -d aula-f75 f65-stock.hex
   ```

## キーマップ

| 場所 | 働き |
| --- | --- |
| ベースレイヤー | 印字どおりの US ANSI 65%。既定は Windows レイヤー。Fn+A で Windows、Fn+S で Mac（保存される） |
| Space の両側 | `ansi`: 印字どおり Alt / Fn / Ctrl（Mac: Command / Fn / Ctrl）。`usjis`: Fn と右 Ctrl の位置が入れ替わり、Space の両隣が IME キーになる。Windows: 左 = 短く押すと無変換、押したままだと Alt。右 = 短く押すと変換、押したままだと右 Ctrl。Mac: 左 = 英数 / Command、右 = かな / 右 Command。約 0.4 秒押し続けるか、他のキーを押した時点で修飾キーになる |
| Fn + 1 … = | Windows: F1〜F12。Mac: 明るさ、Mission Control、Launchpad、前 / 再生 / 次、消音、音量（Apple の並び） |
| Fn + 右 Shift + 1 … = | もう一方の組。Windows ではメディアキー（純正の Fn 列）、Mac では F1〜F12 |
| Fn + Esc | `` ` ``（Shift と一緒で `~`） |
| Fn + Tab | `usjis`: US-JIS のオン / オフ（保存される。Windows レイヤーのみ）。`ansi`: Tab |
| Fn + Q / W / E | Bluetooth スロット 1 / 2 / 3（Bluetooth の位置）: 短く押すと選択、3 秒押すとペアリング（キーが青く点滅） |
| Fn + R | 2.4 GHz: 3 秒押すとドングルとペアリング（キーが点滅） |
| Fn（押している間） | 今つながっているリンクのキーが点く: Y 白（USB）、R 緑（2.4 GHz）、スロットのキー 青（Bluetooth） |
| Fn + B（押している間） | 数字列に電池残量（無線の位置、電池のとき） |
| Fn + \\ , ] , [ | 次の効果（消灯を含めて一周）、次の色（7 色と虹色）、光を全部消す / 戻す |
| Fn + ↑ ↓ ← → | 明るさ（5 段階）と速さ（5 段階。サイドライトも従う） |
| Fn + / . , | サイドライト: 次の効果、色、明るさ |
| Fn + Del / End / PgUp | Insert / Home / `` ` `` |
| Fn + U / I / O | PrtSc / ScrLk / Pause |
| Fn + Backspace を押したまま Fn + V | 設定の初期化（US-JIS オフ、Windows レイヤー、光の初期値） |

どれも USB でも無線でも同じに働きます。光の設定は最後に変えてから約 3 秒で保存され、その瞬間にバックライト全体が一度またたきます（フラッシュへの書き込み）。これは正常です。

## ビルド

`firmware/` のイメージは、下の手順で macOS（ツールチェーンのスクリプト）で作ったリリースビルドです。同じコミットから同じプラットフォームで SDCC 4.5.0 でビルドするとバイト単位で同じものができるので、配布物を自分のビルドと突き合わせて確かめられます。別のプラットフォームでのビルドは数バイト違うことがあります。SDCC のレジスタ割り当てはホストによって同一ではなく（CI の Linux ビルドはこのイメージと 1 つの関数で違います）、そのため CI はバイトを比べる代わりに、自分のリリースビルドでシミュレータのテストを走らせています。

### リリースビルドの手順

1. **ソースを取る。**
   ```sh
   git clone https://github.com/goyamamoto/F65-CustomFirmware.git
   cd F65-CustomFirmware
   ```
2. **ツールチェーンを入れる。** SDCC は **4.5.0** ちょうどが必要です（他の版では smk の `--Werror` で止まるか、違うイメージになります）。ほかに meson、ninja、Python 3。
   - [Nix](https://nixos.org/)（Linux か macOS）: リポジトリで `nix develop`。sinowisp とシミュレータを含めて全部そろいます。
   - Nix なしの macOS: [Homebrew](https://brew.sh/) を入れたうえで、[tools/macos/setup-toolchain.sh](tools/macos/setup-toolchain.sh) を一度実行します。meson と ninja を Homebrew で入れ、SDCC 4.5.0 とシミュレータを `~/.local/smk` にビルドします（時間がかかります）。その後、新しいターミナルを開くたびに:
     ```sh
     . ~/.local/smk/env.sh
     sdcc --version    # 4.5.0 と出ること
     ```
   - それ以外: meson、ninja、Python 3、SDCC 4.5.0 を入れます（パッケージマネージャの版が違うならソースから）。
3. **リリースビルドを設定する**（一度だけ）。`build-release` が出力フォルダ、`--buildtype=release` がデバッグコンソールとログを外す指定です。
   ```sh
   meson setup build-release --buildtype=release
   ```
4. **ビルドする**（欲しいレイアウト、または両方）:
   ```sh
   meson compile -C build-release aula-f65-v1_usjis_smk.hex aula-f65-v1_ansi_smk.hex
   ```
   イメージは `build-release/` にできます。ソースを変えたらこの手順だけをやり直します（手順 3 は不要）。
5. **確かめる**（任意、macOS）。ソースを変えていなければ `firmware/` と同じファイルになります。ビルドしたイメージがそれぞれ `OK` と出ること:
   ```sh
   (cd build-release && shasum -a 256 --ignore-missing -c ../firmware/aula-f65-v1/SHA256SUMS)
   ```
6. **書き込む。** [手順](#手順)の 5 と 6 のとおり、自分のイメージのパスで。例: `sinowisp write -d aula-f75 --force build-release/aula-f65-v1_usjis_smk.hex`。

### リリースビルドとデバッグビルド

| | リリース | デバッグ |
| --- | --- | --- |
| 設定 | `meson setup build-release --buildtype=release` | `meson setup build`（meson の既定） |
| 用途 | 普段使い。`firmware/` のイメージ | 開発 |
| HID デバッグコンソール（`tools/smk-console`） | なし | あり。チップ ID、モードの変化、設定をホストに報告する |
| ログ | なし | あり |
| ソースレベルのシミュレータテスト | スキップ（`.cdb` がない） | 実行 |

キーボードは打ったものを全部見ています。普段使うキーボードにデバッグビルドを入れたままにしないでください。

### シミュレータのテスト

この移植は、パッチを当てた uCsim の上で純正ファームウェアを動かしながら作りました（無線のプロトコル、LED のタイミング、バックライトの効果を純正のイメージとフレーム単位で比べています）。テストにはパッチ済みのシミュレータ（`nix develop` か macOS のスクリプトで入る）と、両方のレイアウトのビルドが要ります。既定では `build/` を使い、`SMK_F65_FIRMWARE`（`usjis`）と `SMK_F65_ANSI_FIRMWARE`（`ansi`）で他のイメージを指せます。

```sh
python3 -m unittest discover -s tests -p test_f65.py            # 基板のテスト（両レイアウト）
python3 -m unittest discover -s tests -p test_f65_usjis.py      # F65 の行列での US-JIS
python3 -m unittest discover -s tests -p test_f65_radio.py      # スイッチ、無線リンク、レポート、電池、スリープ、復旧
python3 -m unittest discover -s tests -p test_f65_fnrow.py      # Fn と右 Shift での数字列
python3 -m unittest discover -s tests -p test_f65_led.py        # LED エンジンの限界
python3 -m unittest discover -s tests -p test_f65_indicators.py # 無線・スリープ・ウォッチドッグと表示灯
python3 -m unittest discover -s tests -p test_f65_backlight.py  # バックライト、そのキー、保存、効果と LED の限界
```

純正ファームウェアと比べるテストには純正のイメージ（`SMK_F65_STOCK_IMAGE`。メーカーの更新ツールの中のファイルで、ここでは配っていません）が要り、なければスキップされます。`meson test -C build-release` は全テストファイルをそのビルドディレクトリのイメージで実行し、イメージがなければスキップでなく失敗にします。

基板の技術メモ（ピン、無線のプロトコル、LED、バックライト、スリープ、復旧、純正との違い全部）は [docs/keyboards/aula-f65-v1.md](docs/keyboards/aula-f65-v1.md)（英語）にあります。上流 smk の README は [docs/README-smk.md](docs/README-smk.md) に残してあります。

## 既知の制限

- どのイメージを書いても設定は初期値に戻ります（sinowisp が設定領域を 0 で埋めるため）: US-JIS オフ、Windows レイヤー、光の初期値。
- 純正から移植していないもの: メーカーのドライバのプロトコル（キーごとのカスタム発光、メーカーのソフトからの設定）、キーごとのカスタム発光スロット、Fn+G のテストモード、電源投入時の光の流れ、USB に切り替えたとき 3 秒間 Y が白く点く表示。
- バックライトは同じ段階でも純正より暗いです（最大で純正の半分ほど）。各 LED を 20 小枠のうち 1 つで点ける方式で、NuPhy Air75 で実績のあるものです。既定の明るさは 4 段階中 2。
- US-JIS は Windows レイヤーだけで働きます。macOS は一部の置き換えに必要な JIS 専用キーを捨ててしまうためです。
- 分かっているのは ANSI の行列だけです（ISO や JIS の F65 は存在しません）。

## 上流 smk からの変更

smk の [69373bb](https://github.com/carlossless/smk/commit/69373bbb633bd1159f4541f486ff7506563a38ec)（2026-09-16）を元に、2026 年 9 月に変更。このツリーには作者の NuPhy Air75 移植（[goyamamoto/NuPhyAir-CustomFirmware](https://github.com/goyamamoto/NuPhyAir-CustomFirmware)）も含まれ、F65 の移植はその上に作られています。

- 新しい基板: `src/keyboards/aula-f65-v1/`（レイアウト `ansi`、`usjis`、LED 診断用の `leddiag`）、`docs/keyboards/aula-f65-v1.md`、`tests/test_f65*.py`、`tests/f65_devices.py`、`tests/f65_radio.py`。基板固有の無線ドライバ（`f65_rf.c`、純正 V1 のプロトコルを EUART0 で）、電源とスリープ（`f65_power.c`、`user_sleep.c`）、LED エンジン（`led.c`）、表示灯（`indicators.c`）、バックライト効果（`backlight.c`、表は純正イメージから）
- 基板が有効にしない限りオフの任意機能: US-JIS（`src/smk/usjis.c/.h`）、モッドタップ（`src/smk/tap_hold.c/.h`）、ブートエスケープ（`BOOT_ESCAPE`）、押したときのキーコードで離す（`LATCH_KEYCODES`）、複数回の走査でのチャタリング取り（`MATRIX_DEBOUNCE_SCANS`）、ウォッチドッグを蹴らない delay（`DELAY_NO_WATCHDOG_KICK`）、フラッシュ操作前のウォッチドッグの蹴り（`FLASH_OP_WATCHDOG_KICK`）、上限つきの PLL ロック待ち（`CLOCK_PLL_WAIT_BOUND`）、厳密な feature report と USB の起動ゲート、1 ms の PWM4 ティックと無線用の EUART0 割り込み優先度 3、キーボードごと・レイアウトごとの `defines`（`meson.build`）
- 共通部分の変更: `tick_scans()`（`src/smk/tick.c/.h`）、行列から分けた `process_keycode()`（`src/smk/matrix.c/.h`）、設定のフィールドと古い長さの記録の読み込み（`src/smk/settings.c/.h`）、PWM と LED のポートをリセット時の状態に戻す起動（`src/sino51lib/startup.c`）、シミュレータの SH68F90 モデルの拡張（EUART0 のバイトタイミングと割り込み優先度、P0 / P4 のピン、PWM4、PWM のトレース、キー接点、INT4 での起床、ウォッチドッグの間隔、実速度の Timer2。`tools/ucsim/`）、テストファイルごとで厳密な `meson test`（`meson.build`、`tests/`）
- `README.md` をこのファイルに置き換え、上流のものは `docs/README-smk.md` へ。新規: `README.ja.md`、`firmware/`、`tools/macos/`

## クレジットとライセンス

- Karolis Stasaitis 氏の [smk](https://github.com/carlossless/smk)、[sinowisp](https://github.com/carlossless/sinowisp)、[sinodude](https://github.com/carlossless/sinodude)。SinoWealth のキーボードに関する氏の仕事がこの移植を可能にしました。
- [Thaolia/smk_aulaF75](https://github.com/Thaolia/smk_aulaF75)（AULA F75 の smk 移植）は、この系統の無線モジュールが EUART0 でどう話すかを示してくれました。
- US-JIS の規則は [goyamamoto/zmk-kb1-usjis](https://github.com/goyamamoto/zmk-kb1-usjis) に従います。
- ライセンス: smk と同じ GPL-2.0（[LICENSE](LICENSE)）。

EPOMAKER と AULA はそれぞれの所有者の商標です。このプロジェクトはそれらと関係なく、承認も受けていません。
