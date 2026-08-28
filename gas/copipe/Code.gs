/**
 * インシデント報告ダイアログ
 * スプレッドシートの拡張機能として動作する。
 * 画面中央に大きなダイアログでフォームを表示し、
 * プルダウン選択 → テンプレート自動生成 → コピペの流れを実現する。
 */

var TEMPLATE_SHEET = "インシデントテンプレ （202411~）";
var KARTE_SHEET = "カルテ項目";

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("インシデント")
    .addItem("報告を作成", "showDialog")
    .addToUi();
}

function showDialog() {
  var html = HtmlService.createHtmlOutputFromFile("Dialog")
    .setWidth(900)
    .setHeight(750);
  SpreadsheetApp.getUi().showModalDialog(html, "インシデント報告");
}

// 商品名リスト
function getProductNames() {
  return [
    "爽軽青汁", "メグレアpremium", "メグレアlight",
    "糖貫プロネス", "肝匠プロネス", "アイゼン",
    "アユミルpremium", "はつらつコラーゲン(クロス用)",
    "すっぽん黒酢", "LIPO CLEAR VITAMIN C",
  ];
}

// カルテテンプレート取得
function getKarteTemplate() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(KARTE_SHEET);
  if (!ws) return "";
  return String(ws.getRange(1, 2).getValue() || "");
}

// テンプレート取得（縦並び構造対応）
function getTemplates() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ws = ss.getSheetByName(TEMPLATE_SHEET);
  if (!ws) return { categories: [], templates: [] };

  var data = ws.getDataRange().getValues();
  if (data.length < 3) return { categories: [], templates: [] };

  var categories = [];
  var templates = [];
  var currentCategory = "";
  var colMap = {};

  for (var r = 0; r < data.length; r++) {
    var row = data[r];
    var cells = row.map(function(c) { return String(c).trim(); });
    var hasData = cells.some(function(c) { return c !== ""; });
    if (!hasData || r === 0) continue;

    var firstCell = cells[0];
    var restEmpty = true;
    for (var c = 1; c < Math.min(cells.length, 8); c++) {
      if (cells[c] !== "") { restEmpty = false; break; }
    }

    if (firstCell && firstCell !== "タイトル" && restEmpty) {
      if (firstCell === "入電時の対応フロー") break;
      currentCategory = firstCell;
      if (categories.indexOf(currentCategory) === -1) categories.push(currentCategory);
      continue;
    }

    if (firstCell === "タイトル") {
      colMap = {};
      for (var ci = 0; ci < cells.length; ci++) {
        var colName = cells[ci].split("\n")[0];
        if (colName) colMap[colName] = ci;
      }
      continue;
    }

    if (!currentCategory || colMap["タイトル"] === undefined) continue;
    var titleIdx = colMap["タイトル"];
    var contentIdx = colMap["内容"];
    if (titleIdx === undefined || contentIdx === undefined) continue;

    var title = titleIdx < cells.length ? cells[titleIdx] : "";
    var content = contentIdx < cells.length ? cells[contentIdx] : "";
    if (!title && !content) continue;

    var vocIdx = colMap["VOC"];
    var voc = vocIdx !== undefined && vocIdx < cells.length ? cells[vocIdx] : "";
    var retIdx = colMap["継続応援結果"];
    var ret = retIdx !== undefined && retIdx < cells.length ? cells[retIdx] : "";

    templates.push({
      category: currentCategory,
      title: title,
      content: content,
      voc: voc,
      retentionResult: ret,
    });
  }
  return { categories: categories, templates: templates };
}
