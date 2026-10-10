# EPOMAKER x AULA F65（V1）カスタムファームウェア

[English](README.md)

EPOMAKER x AULA F65 の最初のモデル（"V1"、"Fn-Ctrl" 版のファームウェアが入っているもの。MCU は BYK916 = SinoWealth SH68F90 系）向けのオープンソースファームウェアです。この 8051 系 MCU では QMK も ZMK も動かないので、Karolis Stasaitis 氏の SinoWealth 8051 用キーボードファームウェア [smk](https://github.com/carlossless/smk) への移植として作りました。このリポジトリは smk に F65 を足したもの（と、その土台にした作者の NuPhy Air75 移植）で、smk が対応する他の機種もそのままビルドできます。

> [!WARNING]
> メーカーのファームウェアを置き換えます。元のファームウェアに戻すには自分の基板のバックアップ（手順 3）が必要で、メーカーのファームウェアはここでは配っていません。ブートローダに戻れないファームウェアは、ハードウェアの書き込み器（例: [sinodude-serial](https://github.com/carlossless/sinodude) を載せた Arduino Nano）でしか復旧できません。その場合に備えて、この移植には起動時の逃げ道（Esc を押しながら電源を入れる）があります。使用は自己責任です。

> [!CAUTION]
> このファームウェアは V1 用です。F65 V2（"Alt-Fn" 版、USB の product が "AULA F65"）では動かしたことがありません。V2 はキーの行と列が同じですが、LED ともう 1 本のピン（P0.3）の扱いが違います。手順 1 で見分けられます。F65 Pro は別のキーボードです。

## できること

- 67 キーすべて、Windows と Mac のレイヤー（Fn+A / Fn+S、保存される）、電源を切っても設定が残る
- USB、Bluetooth（3 スロット、名前は "AULA F65 BT5.0"）、2.4 GHz ドングル。キーボードの無線モジュールを通して
- バックライトの効果（14 種）、色、明るさ、速さ、サイドライト。状態表示（Caps Lock、Fn を押している間のリンクのキー、ペアリング、Fn+B での電池残量、電池低下、充電）をバックライトに重ねて表示
- 電池駆動で約 1 分操作がないとスリープ（キーで起きる）、電池駆動で 30 秒操作がないとバックライトを消灯、電池低下時の停止
- US-JIS（Fn+Tab、`usjis` 系のレイアウト）: ホストを日本語配列にしたまま、US の刻印どおりに入力できる（Windows レイヤー）
- スペースの両隣の IME キー（`usjis` 系のレイアウト）: 単押しで英数 / かな（Mac）、無変換 / 変換（Windows）、長押しで Command / Alt と右 Ctrl
- VIA（`-via` のレイアウト）: [VIA](https://usevia.app) から USB でキー配置を変えられ、フラッシュに保存される
- ブートエスケープ: Esc を押しながら電源を入れると、このファームウェアの USB の処理が動く前にブートローダが起動する
- 有線では元の USB ID のままなので、`sinowisp` が `aula-f75` として見つける

## レイアウトを選ぶ

どのイメージも、ハードウェアの対応、ブートエスケープ、Bluetooth の名前、スリープは同じです。

| レイアウト | 追加されるもの | イメージ |
| --- | --- | --- |
| `ansi` | なし: 刻印どおりの US ANSI | `firmware/` の `aula-f65-v1_ansi_smk.hex` |
| `usjis` | US-JIS（Fn+Tab）、スペースの両隣の IME キー（Fn と右 Ctrl を入れ替え） | `firmware/` の `aula-f65-v1_usjis_smk.hex` |
| `usjis-via` | `usjis` に、VIA でキー配置を変える機能 | `firmware/` の `aula-f65-v1_usjis-via_smk.hex` |
| `ansi-via` | `ansi` に、VIA でキー配置を変える機能（シミュレータでのみ確認） | ビルドして使う（[ビルド](#ビルド)） |

VIA のレイアウトは、最初は `ansi` / `usjis` と同じキー配置です。[VIA を使う](#via-を使う)を見てください。

## 対応キーボード

| キーボード | USB ID | 状態 |
| --- | --- | --- |
| EPOMAKER x AULA F65 の最初のモデル（V1、"Fn-Ctrl" 版）、ANSI | 258a:010c、manufacturer "BY Tech"、product "Gaming Keyboard" | 動作。macOS のホストで USB、Bluetooth、2.4 GHz ドングルを実機で確認 |

対応していないもの: F65 V2（同じ 258a:010c だが product が "AULA F65"、bcdDevice 0x1005）、F65 Pro。

## 手順

1. キーボードを確かめる。裏のスイッチを真ん中（USB）にして挿します。USB ID `258a:010c`、manufacturer "BY Tech"、product "Gaming Keyboard" と出ます（macOS: システム情報 > USB）。product が "AULA F65" なら V2 です。
2. ツールを入れる。[sinowisp](https://github.com/carlossless/sinowisp) を `cargo install sinowisp` で入れます（[Rust](https://rustup.rs/) が必要。sinowisp はキーボードのブートローダを通して USB でフラッシュを読み書きします）。macOS では、入力監視の許可があるターミナルから実行します（システム設定 > プライバシーとセキュリティ > 入力監視）。許可がないとキーボードを開けません。USB ID 258a:010c の他のキーボードは外しておきます（多くのキーボードが同じ ID で、sinowisp は最後に見つけたものを使うため）。
3. 元のファームウェアをバックアップする。このファイルが元に戻す手段になります。
   ```sh
   sinowisp read -d aula-f75 f65-stock.hex                 # ファームウェア。戻すときに使う（手順 8）
   sinowisp read -d aula-f75 -s full f65-stock-full.hex    # ファームウェアとブートローダ。保管用
   ```
   ファームウェアを 2 回読んでファイルを比べると、読み出しが安定しているか分かります。
4. ファームウェアを用意する。レイアウトを選び（上の表）、[firmware/aula-f65-v1](firmware/aula-f65-v1) のイメージを使うか（`shasum -a 256 -c SHA256SUMS` で確認）、自分でビルドします（[ビルド](#ビルド)）。
5. 書き込む。スイッチは真ん中、USB で接続。
   ```sh
   sinowisp write -d aula-f75 --force aula-f65-v1_usjis_smk.hex    # または他のイメージ
   ```
   イメージがフラッシュより小さいので `--force` が要ります。sinowisp は残りを 0 で埋めるので、設定と VIA で変えたキー配置も初期値に戻ります（書くたびに、使うなら Fn+Tab で US-JIS、Fn+S で Mac レイヤー）。
6. 戻り道を確かめる。
   - 新しいファームウェアが動いている状態で `sinowisp read -d aula-f75 check.hex` ができる。
   - USB を抜き（スイッチが真ん中なら電源が切れる）、数秒待ち、Esc を押したまま挿して 2 秒後に離すと、ブートローダの `0603:1020`（"SINO WEALTH" / "Gaming KB"）として見える。Esc なしで挿し直せばファームウェアに戻る。

   2 つ目の方法はこのファームウェアの USB の処理に依存しません。無線の位置からは、先にスイッチを真ん中にします（でないと電池で動き続けます）。
7. 使う。[キーマップ](#キーマップ)を見てください。ドングルは 2.4 GHz の位置で Fn+R を 3 秒、Bluetooth のスロットは Bluetooth の位置で Fn+Q / W / E を 3 秒押してペアリングします。
8. 元のファームウェアに戻す（いつでも。スイッチは真ん中）:
   ```sh
   sinowisp write -d aula-f75 f65-stock.hex
   ```

## キーマップ

| 場所 | 働き |
| --- | --- |
| ベースレイヤー | 印字どおりの US ANSI 65%。既定は Windows レイヤー。Fn+A で Windows、Fn+S で Mac（保存される） |
| Space の両側 | `ansi`: 印字どおり Alt / Fn / Ctrl（Mac: Command / Fn / Ctrl）。`usjis`: Fn と右 Ctrl の位置が入れ替わり、Space の両隣が IME キーになる。Windows: 左 = 短く押すと無変換、押したままだと Alt。右 = 短く押すと変換、押したままだと右 Ctrl。Mac: 左 = 英数 / Command、右 = かな / 右 Command。約 0.4 秒押し続けるか、他のキーを押した時点で修飾キーになる |
| Fn + 1 … = | Windows: F1〜F12。Mac: 明るさ、Mission Control、Launchpad、前 / 再生 / 次、消音、音量（Apple の並び） |
| Fn + 右 Shift + 1 … = | もう一方の組。Windows ではメディアキー、Mac では F1〜F12 |
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
| Fn + Backspace を押したまま Fn + V | 設定の初期化（US-JIS オフ、Windows レイヤー、光の初期値。VIA のレイアウトでは組み込みのキー配置にも戻る） |

どれも USB でも無線でも同じに働きます。光の設定は最後に変えてから約 3 秒で保存され、その瞬間にバックライト全体が一度またたきます（フラッシュへの書き込み）。

## VIA を使う

`-via` のイメージを書き、スイッチを真ん中（USB）にして:

1. WebHID が使えるブラウザ（Chrome、Edge）で [usevia.app](https://usevia.app) を開く。
2. Settings で「Show Design tab」をオンにし、Design でこのリポジトリの [via.json](src/keyboards/aula-f65-v1/via.json) を読み込む。
3. Configure で「Authorize device」を押し、「AULA F65 (SMK)」を選ぶ。

4 つのレイヤーは Windows の基本、Mac の基本、Windows の Fn、Mac の Fn です。変更は最後の変更から約 0.5 秒後にフラッシュに保存され、Bluetooth と 2.4 GHz でも使われます。VIA で設定できるのは、普通のキー、修飾付きのキー（Ctrl+C など）、モッドタップ、`MO`、メディアキー、このキーボードのキー（US-JIS、Win / Mac、リンク、電池、Fn の数字列、照明）です。レイヤータップ、トグル、ワンショット、マクロには対応しておらず、照明は Fn のキーで操作します。詳しくは [docs/keyboards/aula-f65-v1.md](docs/keyboards/aula-f65-v1.md#via)（英語）。

## ビルド

`firmware/` のイメージは、下の手順で macOS（ツールチェーンのスクリプト）で作ったリリースビルドです。同じコミットから同じプラットフォームで SDCC 4.5.0 でビルドするとバイト単位で同じものができるので、配布物を自分のビルドと突き合わせて確かめられます。別のプラットフォームでのビルドは数バイト違うことがあります。SDCC のレジスタ割り当てはホストによって同一ではなく（CI の Linux ビルドはこのイメージと 1 つの関数で違います）、そのため CI はバイトを比べる代わりに、自分のリリースビルドでシミュレータのテストを走らせています。

### リリースビルドの手順

1. ソースを取る。
   ```sh
   git clone https://github.com/goyamamoto/F65-CustomFirmware.git
   cd F65-CustomFirmware
   ```
2. ツールチェーンを入れる。SDCC は 4.5.0 ちょうどが必要です（他の版では smk の `--Werror` で止まるか、違うイメージになります）。ほかに meson、ninja、Python 3。
   - [Nix](https://nixos.org/)（Linux か macOS）: リポジトリで `nix develop`。sinowisp とシミュレータを含めて全部そろいます。
   - Nix なしの macOS: [Homebrew](https://brew.sh/) を入れたうえで、[tools/macos/setup-toolchain.sh](tools/macos/setup-toolchain.sh) を一度実行します。meson と ninja を Homebrew で入れ、SDCC 4.5.0 とシミュレータを `~/.local/smk` にビルドします（時間がかかります）。その後、新しいターミナルを開くたびに:
     ```sh
     . ~/.local/smk/env.sh
     sdcc --version    # 4.5.0 と出ること
     ```
   - それ以外: meson、ninja、Python 3、SDCC 4.5.0 を入れます（パッケージマネージャの版が違うならソースから）。
3. リリースビルドを設定する（一度だけ）。`build-release` が出力フォルダ、`--buildtype=release` がデバッグコンソールとログを外す指定です。
   ```sh
   meson setup build-release --buildtype=release
   ```
4. 欲しいレイアウトをビルドする:
   ```sh
   meson compile -C build-release aula-f65-v1_usjis_smk.hex aula-f65-v1_ansi_smk.hex aula-f65-v1_usjis-via_smk.hex aula-f65-v1_ansi-via_smk.hex
   ```
   イメージは `build-release/` にできます。ソースを変えたらこの手順だけをやり直します（手順 3 は不要）。
5. 確かめる（任意、macOS）。ソースを変えていなければ `firmware/` と同じファイルになります。ビルドしたイメージがそれぞれ `OK` と出ること:
   ```sh
   (cd build-release && shasum -a 256 --ignore-missing -c ../firmware/aula-f65-v1/SHA256SUMS)
   ```
6. 書き込む。[手順](#手順)の 5 と 6 のとおり、自分のイメージのパスで。例: `sinowisp write -d aula-f75 --force build-release/aula-f65-v1_usjis_smk.hex`。

### リリースビルドとデバッグビルド

| | リリース | デバッグ |
| --- | --- | --- |
| 設定 | `meson setup build-release --buildtype=release` | `meson setup build`（meson の既定） |
| 用途 | 普段使い。`firmware/` のイメージ | 開発 |
| HID デバッグコンソール（`tools/smk-console`） | なし | あり。チップ ID、モードの変化、設定をホストに報告する |
| ログ | なし | あり |
| ソースレベルのシミュレータテスト | スキップ（`.cdb` がない） | 実行 |

キーボードは打ったものを全部見ています。デバッグビルドはコンソールでホストに報告し、リリースビルドにはコンソールがありません。

### シミュレータのテスト

テストはパッチを当てた uCsim の上でイメージを動かします。パッチ済みのシミュレータ（`nix develop` か macOS のスクリプトで入る）と、レイアウトのビルドが要ります。既定では `build/` を使い、`SMK_F65_FIRMWARE`（`usjis`）と `SMK_F65_ANSI_FIRMWARE`（`ansi`）で他のイメージを指せます。

```sh
python3 -m unittest discover -s tests -p test_f65.py            # 基板のテスト（両レイアウト）
python3 -m unittest discover -s tests -p test_f65_usjis.py      # F65 の行列での US-JIS
python3 -m unittest discover -s tests -p test_f65_radio.py      # スイッチ、無線リンク、レポート、電池、スリープ、復旧
python3 -m unittest discover -s tests -p test_f65_fnrow.py      # Fn と右 Shift での数字列
python3 -m unittest discover -s tests -p test_f65_led.py        # LED エンジンの限界
python3 -m unittest discover -s tests -p test_f65_indicators.py # 無線・スリープ・ウォッチドッグと表示灯
python3 -m unittest discover -s tests -p test_f65_backlight.py  # バックライト、そのキー、保存、効果と LED の限界
python3 -m unittest discover -s tests -p test_f65_via.py        # VIA のレイアウト（SMK_F65_VIA_FIRMWARE、SMK_F65_ANSI_VIA_FIRMWARE）
```

一部のチェックは、元のファームウェアを同じシミュレータで動かして比べます。そのイメージ（`SMK_F65_STOCK_IMAGE`。メーカーの更新ツールの中のファイルで、ここでは配っていません）が要り、なければスキップされます。`meson test -C build-release` は全テストファイルをそのビルドディレクトリのイメージで実行し、イメージがなければスキップでなく失敗にします。

基板の技術メモ（ピン、無線リンク、LED、バックライト、スリープ、復旧、VIA、元のファームウェアとの違い）は [docs/keyboards/aula-f65-v1.md](docs/keyboards/aula-f65-v1.md)（英語）にあります。上流 smk の README は [docs/README-smk.md](docs/README-smk.md) に残してあります。

## 既知の制限

- どのイメージを書いても設定は初期値に戻ります（sinowisp がフラッシュを消去し、イメージを 0 で埋めて書くため）: US-JIS オフ、Windows レイヤー、光の初期値、VIA で変えたキー配置。
- 含めていないもの: メーカーのドライバのプロトコル（キーごとのカスタム発光、メーカーのソフトからの設定）、Fn+G のテストモード、電源投入時の光の流れ、USB に切り替えたとき 3 秒間 Y が白く点く表示。
- バックライトは元のファームウェアより暗めです（最大で 3 分の 2 ほど）。各 LED を 200 µs の 20 小枠のうち 1 つで点け、その間にキー走査を挟む方式です。既定の明るさは 4 段階中 2。
- US-JIS は Windows レイヤーだけで働きます。macOS は一部の置き換えに必要な JIS 専用キーを捨ててしまうためです。
- VIA: マクロと照明のメニューはありません。キー配置を変えられるのは USB（スイッチが真ん中）のときだけです。同じキーを送る 2 つのキー（例: C と Ctrl+C）はそのキーを共有し、片方を離すと両方とも離したことになります。
- 分かっているのは ANSI の行列だけです（ISO や JIS の F65 は存在しません）。

## 上流 smk からの変更

smk の [69373bb](https://github.com/carlossless/smk/commit/69373bbb633bd1159f4541f486ff7506563a38ec)（2026-09-16）を元に、2026 年 9 月から 10 月に変更。このツリーには作者の NuPhy Air75 移植（[goyamamoto/NuPhyAir-CustomFirmware](https://github.com/goyamamoto/NuPhyAir-CustomFirmware)）も含まれ、F65 の移植はその上に作られています。

- 新しい基板: `src/keyboards/aula-f65-v1/`（レイアウト `ansi`、`usjis`、`ansi-via`、`usjis-via`、LED 診断用の `leddiag`。`via.json`）、`docs/keyboards/aula-f65-v1.md`、`tests/test_f65*.py`、`tests/f65_devices.py`、`tests/f65_radio.py`。基板固有の無線ドライバ（`f65_rf.c`、モジュールのプロトコルを EUART0 で）、電源とスリープ（`f65_power.c`、`user_sleep.c`）、LED エンジン（`led.c`）、表示灯（`indicators.c`）、バックライト効果（`backlight.c`。`backlight_tables.h` は元のファームウェアの効果の表）
- 基板が有効にしない限りオフの任意機能: VIA の raw HID コマンドとフラッシュに保存するキー配置（`VIA_ENABLE`: `src/smk/via.c/.h`、`src/smk/dynamic_keymap.c/.h`、`src/smk/usb.c` の USB 構成）、US-JIS（`src/smk/usjis.c/.h`）、モッドタップ（`src/smk/tap_hold.c/.h`）、ブートエスケープ（`BOOT_ESCAPE`）、押したときのキーコードで離す（`LATCH_KEYCODES`）、複数回の走査でのチャタリング取り（`MATRIX_DEBOUNCE_SCANS`）、ウォッチドッグを蹴らない delay（`DELAY_NO_WATCHDOG_KICK`）、フラッシュ操作前のウォッチドッグの蹴り（`FLASH_OP_WATCHDOG_KICK`）、上限つきの PLL ロック待ち（`CLOCK_PLL_WAIT_BOUND`）、厳密な feature report と USB の起動ゲート、1 ms の PWM4 ティックと無線用の EUART0 割り込み優先度 3、キーボードごと・レイアウトごとの `defines`（`meson.build`）
- 共通部分の変更: `tick_scans()`（`src/smk/tick.c/.h`）、行列から分けた `process_keycode()`（`src/smk/matrix.c/.h`）、設定のフィールドと古い長さの記録の読み込み（`src/smk/settings.c/.h`）、PWM と LED のポートをリセット時の状態に戻す起動（`src/sino51lib/startup.c`）、シミュレータの SH68F90 モデルの拡張（EUART0 のバイトタイミングと割り込み優先度、P0 / P4 のピン、PWM4、PWM のトレース、キー接点、INT4 での起床、ウォッチドッグの間隔、実速度の Timer2、EP2 OUT。`tools/ucsim/`）、テストファイルごとで厳密な `meson test`（`meson.build`、`tests/`）
- `README.md` をこのファイルに置き換え、上流のものは `docs/README-smk.md` へ。新規: `README.ja.md`、`firmware/`、`tools/macos/`

## クレジットとライセンス

- Karolis Stasaitis 氏の [smk](https://github.com/carlossless/smk)、[sinowisp](https://github.com/carlossless/sinowisp)、[sinodude](https://github.com/carlossless/sinodude)。SinoWealth のキーボードに関する氏の仕事がこの移植を可能にしました。
- [Thaolia/smk_aulaF75](https://github.com/Thaolia/smk_aulaF75)（AULA F75 の smk 移植）は、この系統の無線モジュールが EUART0 でどう話すかを示してくれました。
- VIA のコマンド処理とキー配置のインタフェースは QMK の `quantum/via.c` と `quantum/dynamic_keymap.c`（[QMK](https://github.com/qmk/qmk_firmware)、GPL-2.0-or-later）に従います。設定ツールは [VIA](https://github.com/the-via/app) です。
- US-JIS の規則は [goyamamoto/zmk-kb1-usjis](https://github.com/goyamamoto/zmk-kb1-usjis) に従います。
- ライセンス: smk と同じ GPL-2.0（[LICENSE](LICENSE)）。

EPOMAKER と AULA はそれぞれの所有者の商標です。このプロジェクトはそれらと関係なく、承認も受けていません。
