/**
 * インシデント報告サイドバー
 * スプレッドシートの拡張機能として動作する。
 * 「インシデントテンプレ （202411~）」シートからテンプレートを読み込み、
 * OPがカテゴリ・商品名を選んで対応記録を作成する。
 *
 * シート構造（縦並び）:
 *   カテゴリ名の行 → 列ヘッダーの行 → データ行 → 空行 → 次のカテゴリ…
 */

// メニュー追加
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("インシデント")
    .addItem("報告を作成", "showSidebar")
    .addSeparator()
    .addItem("テストA（サイドバー）", "showTestA")
    .addItem("テストB（ダイアログ）", "showTestB")
    .addToUi();
}

// 既存サイドバー
function showSidebar() {
  var html = HtmlService.createHtmlOutputFromFile("Sidebar")
    .setTitle("インシデント報告")
    .setWidth(400);
  SpreadsheetApp.getUi().showSidebar(html);
}

// テストA: 新サイドバー（タブ付き）
function showTestA() {
  var html = HtmlService.createHtmlOutputFromFile("SidebarTestA")
    .setTitle("テストA - インシデント報告")
    .setWidth(400);
  SpreadsheetApp.getUi().showSidebar(html);
}

// テストB: 新ダイアログ（2カラム）
function showTestB() {
  var html = HtmlService.createHtmlOutputFromFile("DialogTestB")
    .setWidth(1050)
    .setHeight(780);
  SpreadsheetApp.getUi().showModalDialog(html, "テストB - インシデント報告");
}

// シート名
var TEMPLATE_SHEET = "インシデントテンプレ （202411~）";
var KARTE_SHEET = "カルテ項目";
var VOC_SHEET = "VOC/集計区分(20260128~)";
var RETENTION_POLICY_SHEET = "継続応援施策DB";

// 商品シートの一覧（これらのシート名を商品選択肢として使う）
var PRODUCT_SHEETS = [
  "爽軽青汁",
  "メグレアpremium",
  "メグレアlight",
  "糖貫プロネス",
  "肝匠プロネス",
  "アイゼン",
  "アユミルpremium",
  "はつらつコラーゲン(クロス用)",
  "すっぽん黒酢",
  "LIPO CLEAR VITAMIN C",
];

/**
 * 商品名リストを返す
 */
function getProductNames() {
  return PRODUCT_SHEETS;
}

/**
 * カルテテンプレートを返す
 */
function getKarteTemplate() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(KARTE_SHEET);
  if (!ws) return "";
  return String(ws.getRange(1, 2).getValue() || "");
}

/**
 * 継続応援施策DBから施策一覧を取得する。
 * 戻り値: [{ name: "施策名", timing: "F1~", course: "全て" }, ...]
 */
function getRetentionPolicies() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(RETENTION_POLICY_SHEET);
  if (!ws) return [];

  var data = ws.getDataRange().getValues();
  var policies = [];
  for (var r = 1; r < data.length; r++) {
    var name = String(data[r][0] || "").trim();
    var timing = String(data[r][1] || "").trim();
    var course = String(data[r][2] || "").trim();
    if (!name) continue;
    policies.push({ name: name, timing: timing, course: course });
  }
  return policies;
}

/**
 * VOC/集計区分シートから選択肢を動的に取得する。
 * B列のヘッダー行をキーに検索し、次のセクションまでのB列の値を抽出する。
 * シートに行を追加/削除するだけで自動反映される。
 */
function getChoices() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(VOC_SHEET);
  if (!ws) return { cancelReasons: [], successDetails: [], stopReasons: [] };

  var data = ws.getDataRange().getValues();

  // ヘッダーのキーワード → 結果キーのマッピング
  var sections = {
    "解約希望理由": "cancelReasons",
    "継続応援成功内訳": "successDetails",
    "継続応援中断理由": "stopReasons",
  };

  var result = { cancelReasons: [], successDetails: [], stopReasons: [] };
  var currentKey = "";

  for (var r = 0; r < data.length; r++) {
    var a = String(data[r][0] || "").trim();
    var b = String(data[r][1] || "").trim();

    // ヘッダー行の検出: B列に「解約希望理由」「継続応援成功内訳」「継続応援中断理由」を含む
    var foundSection = false;
    for (var keyword in sections) {
      if (b.indexOf(keyword) >= 0) {
        currentKey = sections[keyword];
        foundSection = true;
        break;
      }
    }
    if (foundSection) continue;

    // 別セクションのヘッダーに到達したらリセット
    if (a === "コード" && (b.indexOf("集計区分") >= 0 || b.indexOf("ボタン") >= 0)) {
      currentKey = "";
      continue;
    }

    // データ行: currentKeyが設定されていてB列に値がある場合
    if (currentKey && b && b !== "該当なし") {
      result[currentKey].push(b);
    }
    // 「該当なし」は最後に追加するため別扱い
    if (currentKey && b === "該当なし") {
      result[currentKey].push(b);
    }

    // 空行でセクション終了
    if (!a && !b) {
      currentKey = "";
    }
  }

  return result;
}

/**
 * テンプレートシートを読み込み、縦並びブロック構造をパースして返す。
 *
 * 構造:
 *   行: カテゴリ名（A列のみに値、B列以降は空）
 *   行: 列ヘッダー（タイトル, 内容, VOC, ...）
 *   行: データ（テンプレート）
 *   行: データ
 *   行: 空行
 *   行: 次のカテゴリ名
 *   ...
 *
 * 戻り値: { categories: [...], templates: [...] }
 */
function getTemplates() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(TEMPLATE_SHEET);
  if (!ws) {
    return { categories: [], templates: [] };
  }

  var data = ws.getDataRange().getValues();
  if (data.length < 3) {
    return { categories: [], templates: [] };
  }

  var categories = [];
  var templates = [];
  var currentCategory = "";
  var colMap = {}; // 列名 → インデックス

  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    var cells = row.map(function(c) { return String(c).trim(); });

    // 空行 → ブロック区切り
    var hasData = cells.some(function(c) { return c !== ""; });
    if (!hasData) {
      continue;
    }

    // 行1は注意書き（スキップ）
    if (r === 0) continue;

    // カテゴリ名の行を判定:
    // A列に値があり、かつ A列が「タイトル」でなく、B列以降がほぼ空
    var firstCell = cells[0];
    var restEmpty = true;
    for (var c = 1; c < Math.min(cells.length, 8); c++) {
      if (cells[c] !== "") {
        restEmpty = false;
        break;
      }
    }

    if (firstCell && firstCell !== "タイトル" && restEmpty) {
      // 「入電時の対応フロー」以降はテンプレートではないので終了
      if (firstCell === "入電時の対応フロー") break;

      currentCategory = firstCell;
      if (categories.indexOf(currentCategory) === -1) {
        categories.push(currentCategory);
      }
      continue;
    }

    // 列ヘッダー行の判定: A列が「タイトル」
    if (firstCell === "タイトル") {
      colMap = {};
      for (var ci = 0; ci < cells.length; ci++) {
        var colName = cells[ci].split("\n")[0];
        if (colName) {
          colMap[colName] = ci;
        }
      }
      continue;
    }

    // データ行
    if (!currentCategory || colMap["タイトル"] === undefined) continue;

    var titleIdx = colMap["タイトル"];
    var contentIdx = colMap["内容"];
    if (titleIdx === undefined || contentIdx === undefined) continue;

    var title = titleIdx < cells.length ? cells[titleIdx] : "";
    var content = contentIdx < cells.length ? cells[contentIdx] : "";
    if (!title && !content) continue;

    var vocIdx = colMap["VOC"];
    var voc = vocIdx !== undefined && vocIdx < cells.length ? cells[vocIdx] : "";

    var orderStatus = "";
    var retentionResult = "";
    var cancelReason = "";
    var extras = {};

    for (var key in colMap) {
      if (key === "タイトル" || key === "内容" || key === "VOC") continue;
      var idx = colMap[key];
      var val = idx < cells.length ? cells[idx] : "";
      if (!val) continue;

      if (key.indexOf("注文状況") >= 0) {
        orderStatus = val;
      } else if (key.indexOf("継続応援結果") >= 0) {
        retentionResult = val;
      } else if (key.indexOf("解約希望理由") >= 0) {
        cancelReason = val;
      } else {
        extras[key] = val;
      }
    }

    templates.push({
      category: currentCategory,
      title: title,
      content: content,
      voc: voc,
      orderStatus: orderStatus,
      retentionResult: retentionResult,
      cancelReason: cancelReason,
      extras: extras,
    });
  }

  return { categories: categories, templates: templates };
}
