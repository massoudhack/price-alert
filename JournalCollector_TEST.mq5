//+------------------------------------------------------------------+
//|  JournalCollector.mq5                                            |
//|  ارسال تاریخچه معاملات + کندل‌ها به Flask Journal               |
//|  نسخه: 1.0  — MQL5 / MetaTrader 5                               |
//+------------------------------------------------------------------+
#property copyright "PriceAlert Journal"
#property version   "1.00-TEST"
#property description "نسخه تست — فقط لاگ، بدون ذخیره در سایت"

#include <Trade\Trade.mqh>

//===================================================================
// تنظیمات — اینجا رو پر کن
//===================================================================
input string SERVER_URL      = "https://YOUR-APP.onrender.com"; // آدرس Flask بدون /
input string API_SECRET      = "";                               // اگه endpoint امن کردی (خالی بذار)
input int    DAYS_BACK       = 7;                                // چند روز گذشته بررسی شود
input string TIMEFRAME_STR   = "1h";                            // تایم‌فریم: 1m 5m 15m 1h 4h 1d
input int    CANDLES_BEFORE  = 30;                               // کندل قبل از ورود
input int    CANDLES_AFTER   = 60;                               // کندل بعد از خروج
input bool   SEND_ON_ATTACH  = true;                             // وقتی EA وصل میشه بفرسته
input bool   LOG_VERBOSE     = true;                             // لاگ کامل

//===================================================================
// متغیرهای داخلی
//===================================================================
datetime g_lastRun = 0;
string   g_sentFile = "JC_sent_tickets_mt5.txt";

//+------------------------------------------------------------------+
int OnInit()
  {
   Print("=== JournalCollector MT5 v1.0 شروع شد ===");
   Print("Server: ", SERVER_URL);
   Print("Days Back: ", DAYS_BACK, " | TF: ", TIMEFRAME_STR);

   // دکمه روی چارت
   DrawButton();
   ChartSetInteger(0, CHART_EVENT_OBJECT_DELETE, true);

   if(SEND_ON_ATTACH)
     {
      Sleep(2000);
      CollectAndSend();
     }

   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectDelete(0, "JC_SendBtn");
   ObjectDelete(0, "JC_StatusLbl");
   Print("=== JournalCollector MT5 متوقف شد ===");
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   // هر 6 ساعت خودکار
   if(TimeCurrent() - g_lastRun > 21600 && g_lastRun > 0)
     {
      CollectAndSend();
     }
  }

//+------------------------------------------------------------------+
// تبدیل رشته به ENUM_TIMEFRAMES
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StrToTF(string tf)
  {
   if(tf == "1m"  || tf == "M1")  return PERIOD_M1;
   if(tf == "5m"  || tf == "M5")  return PERIOD_M5;
   if(tf == "15m" || tf == "M15") return PERIOD_M15;
   if(tf == "30m" || tf == "M30") return PERIOD_M30;
   if(tf == "1h"  || tf == "H1")  return PERIOD_H1;
   if(tf == "4h"  || tf == "H4")  return PERIOD_H4;
   if(tf == "1d"  || tf == "D1")  return PERIOD_D1;
   return PERIOD_H1;
  }

//+------------------------------------------------------------------+
// تبدیل ENUM_TIMEFRAMES به رشته
//+------------------------------------------------------------------+
string TFToStr(ENUM_TIMEFRAMES tf)
  {
   switch(tf)
     {
      case PERIOD_M1:  return "1m";
      case PERIOD_M5:  return "5m";
      case PERIOD_M15: return "15m";
      case PERIOD_M30: return "30m";
      case PERIOD_H1:  return "1h";
      case PERIOD_H4:  return "4h";
      case PERIOD_D1:  return "1d";
      default:         return "1h";
     }
  }

//+------------------------------------------------------------------+
// تبدیل datetime به رشته تهران (UTC+3:30)
//+------------------------------------------------------------------+
string FormatTehran(datetime utc_time)
  {
   // تهران = UTC + 3:30
   datetime teh = utc_time + 3*3600 + 30*60;
   MqlDateTime mdt;
   TimeToStruct(teh, mdt);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",
                       mdt.year, mdt.mon, mdt.day,
                       mdt.hour, mdt.min, mdt.sec);
  }

//+------------------------------------------------------------------+
// Escape JSON string
//+------------------------------------------------------------------+
string EscapeJSON(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   StringReplace(s, "\n", "\\n");
   StringReplace(s, "\r", "\\r");
   StringReplace(s, "\t", "\\t");
   return s;
  }

//+------------------------------------------------------------------+
// double به string با دقت مناسب
//+------------------------------------------------------------------+
string D2S(double v, int digits = 5)
  {
   if(v == 0.0 || v == EMPTY_VALUE) return "null";
   return DoubleToString(v, digits);
  }

//+------------------------------------------------------------------+
// ساخت JSON کندل‌ها برای یک معامله
//+------------------------------------------------------------------+
string GetCandleSnapshot(string sym, ENUM_TIMEFRAMES tf, datetime entry_time, datetime exit_time)
  {
   // اطمینان از لود بودن سمبل
   if(!SymbolSelect(sym, true))
     {
      Print("[EA] Symbol not in MarketWatch: ", sym);
      return "[]";
     }

   // از چند کندل قبل از ورود تا چند کندل بعد از خروج
   datetime from_time = entry_time - (datetime)(CANDLES_BEFORE * PeriodSeconds(tf));
   datetime to_time   = exit_time  + (datetime)(CANDLES_AFTER  * PeriodSeconds(tf));
   // to_time حداکثر الان
   if(to_time > TimeCurrent()) to_time = TimeCurrent();

   // دریافت کندل‌ها
   MqlRates rates[];
   ArraySetAsSeries(rates, false); // از قدیم به جدید
   int copied = CopyRates(sym, tf, from_time, to_time, rates);

   if(copied <= 0)
     {
      Print("[EA] CopyRates failed for ", sym, " copied=", copied, " err=", GetLastError());
      return "[]";
     }

   if(LOG_VERBOSE)
      Print("[EA] Snapshot: ", sym, " candles=", copied);

   string arr = "[";
   for(int i = 0; i < copied; i++)
     {
      if(i > 0) arr += ",";
      arr += StringFormat(
               "{\"t\":%d,\"o\":%.5f,\"h\":%.5f,\"l\":%.5f,\"c\":%.5f,\"v\":%d}",
               (long)rates[i].time,
               rates[i].open,
               rates[i].high,
               rates[i].low,
               rates[i].close,
               (long)rates[i].tick_volume
             );
     }
   arr += "]";
   return arr;
  }

//+------------------------------------------------------------------+
// نتیجه معامله از سود
//+------------------------------------------------------------------+
string GetOutcome(double profit)
  {
   if(profit > 0.01)  return "win";
   if(profit < -0.01) return "loss";
   return "breakeven";
  }

//+------------------------------------------------------------------+
// HTTP POST
//+------------------------------------------------------------------+
bool PostJSON(string endpoint, string json_body, string &response)
  {
   string url = SERVER_URL + endpoint;

   // هدرها
   string headers = "Content-Type: application/json\r\n";
   if(StringLen(API_SECRET) > 0)
      headers += "X-API-Secret: " + API_SECRET + "\r\n";

   // تبدیل string به char array
   uchar post_data[];
   uchar result_data[];
   string result_headers;

   StringToCharArray(json_body, post_data, 0, StringLen(json_body), CP_UTF8);

   // حذف null terminator آخر
   int sz = ArraySize(post_data);
   if(sz > 0 && post_data[sz-1] == 0)
      ArrayResize(post_data, sz - 1);

   ResetLastError();
   int res = WebRequest("POST", url, headers, 20000, post_data, result_data, result_headers);

   if(res == -1)
     {
      int err = GetLastError();
      Print("[EA] ❌ WebRequest خطا: ", err);
      if(err == 4014)
         Print("[EA] ⚠️ URL رو در Tools > Options > Expert Advisors اضافه کن: ", SERVER_URL);
      return false;
     }

   response = CharArrayToString(result_data, 0, WHOLE_ARRAY, CP_UTF8);

   if(res != 200 && res != 201)
     {
      Print("[EA] HTTP ", res, ": ", StringSubstr(response, 0, 120));
      return false;
     }

   if(LOG_VERBOSE)
      Print("[EA] ✅ POST OK → ", StringSubstr(response, 0, 100));

   return true;
  }

//+------------------------------------------------------------------+
// چک کردن ticket در فایل لوکال
//+------------------------------------------------------------------+
bool IsAlreadySent(ulong ticket)
  {
   int handle = FileOpen(g_sentFile, FILE_READ | FILE_TXT | FILE_SHARE_READ | FILE_ANSI);
   if(handle == INVALID_HANDLE) return false;

   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle);
   FileClose(handle);

   return (StringFind(content, "|" + IntegerToString(ticket) + "|") >= 0);
  }

//+------------------------------------------------------------------+
// ذخیره ticket
//+------------------------------------------------------------------+
void MarkAsSent(ulong ticket)
  {
   int handle = FileOpen(g_sentFile, FILE_READ | FILE_WRITE | FILE_TXT | FILE_SHARE_READ | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      handle = FileOpen(g_sentFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE) return;

   FileSeek(handle, 0, SEEK_END);
   FileWriteString(handle, "|" + IntegerToString(ticket) + "|");
   FileClose(handle);
  }

//+------------------------------------------------------------------+
// ارسال یک deal (معامله بسته‌شده در MT5)
//+------------------------------------------------------------------+
bool SendDeal(ulong deal_ticket)
  {
   // در MT5 تاریخچه با HistoryDeal کار میکنه
   if(!HistoryDealSelect(deal_ticket))
     {
      Print("[EA] HistoryDealSelect failed: ", deal_ticket);
      return false;
     }

   // نوع deal — فقط entry اصلی رو بگیر (DEAL_ENTRY_IN یا DEAL_ENTRY_INOUT)
   ENUM_DEAL_ENTRY deal_entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
   ENUM_DEAL_TYPE  deal_type  = (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal_ticket, DEAL_TYPE);

   // فقط BUY/SELL — بقیه (balance, credit) رو رد کن
   if(deal_type != DEAL_TYPE_BUY && deal_type != DEAL_TYPE_SELL) return false;

   string sym       = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
   double lots      = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
   double price_in  = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
   double profit    = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
   double swap      = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
   double commission= HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
   datetime deal_time = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
   string comment   = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
   long   position_id = HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);

   double total_profit = profit + swap + commission;
   string direction    = (deal_type == DEAL_TYPE_BUY) ? "BUY" : "SELL";

   // پیدا کردن deal خروج برای همین position
   // در MT5 هر position یه deal ورود و یه deal خروج داره
   double sl_price = 0, tp_price = 0;
   double exit_price = 0;
   datetime exit_time = 0;

   // اگه DEAL_ENTRY_OUT بود — این deal خروجه، نه ورود
   // ما deal ورود رو میخوایم برای entry price
   // بهترین راش: از position history بخون

   // جستجو در تاریخچه برای پیدا کردن ورود/خروج این position
   double entry_price  = price_in;
   datetime entry_time = deal_time;
   string outcome      = "";

   // اگه این deal خروجه، پیدا کن ورودش رو
   if(deal_entry == DEAL_ENTRY_OUT || deal_entry == DEAL_ENTRY_INOUT)
     {
      // این deal بسته‌کننده position است — ما نمیخوایم deal خروج رو جداگانه بفرستیم
      // چون position کامل رو میفرستیم از طریق position_id
      return false; // از position-based ارسال استفاده میکنیم
     }

   // اگه DEAL_ENTRY_IN هست — این ورود یه position جدیده
   // بگرد دنبال deal خروج این position
   bool found_exit = false;
   int total_deals = HistoryDealsTotal();
   for(int j = 0; j < total_deals; j++)
     {
      ulong d = HistoryDealGetTicket(j);
      if(HistoryDealGetInteger(d, DEAL_POSITION_ID) != position_id) continue;
      ENUM_DEAL_ENTRY de = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY);
      if(de == DEAL_ENTRY_OUT || de == DEAL_ENTRY_INOUT)
        {
         exit_price  = HistoryDealGetDouble(d, DEAL_PRICE);
         exit_time   = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
         double ep   = HistoryDealGetDouble(d, DEAL_PROFIT);
         double es   = HistoryDealGetDouble(d, DEAL_SWAP);
         double ec   = HistoryDealGetDouble(d, DEAL_COMMISSION);
         total_profit = ep + es + ec;
         found_exit   = true;
         break;
        }
     }

   // اگه خروج پیدا نشد — position هنوز باز است
   if(!found_exit) return false;

   outcome = GetOutcome(total_profit);

   // SL/TP از position
   // در MT5 از HistoryOrderSelect میشه SL/TP رو گرفت
   // اما راحت‌تر اینه که از deal comment یا order بخونیم
   // فعلاً از HistoryOrders میخونیم
   ulong pos_order = 0;
   int total_orders = HistoryOrdersTotal();
   for(int k = 0; k < total_orders; k++)
     {
      ulong ord = HistoryOrderGetTicket(k);
      if(HistoryOrderGetInteger(ord, ORDER_POSITION_ID) == position_id)
        {
         ENUM_ORDER_TYPE ot = (ENUM_ORDER_TYPE)HistoryOrderGetInteger(ord, ORDER_TYPE);
         if(ot == ORDER_TYPE_BUY || ot == ORDER_TYPE_SELL)
           {
            sl_price = HistoryOrderGetDouble(ord, ORDER_SL);
            tp_price = HistoryOrderGetDouble(ord, ORDER_TP);
            break;
           }
        }
     }

   // تایم‌فریم
   ENUM_TIMEFRAMES tf = StrToTF(TIMEFRAME_STR);
   string tf_str      = TFToStr(tf);

   // کندل‌ها
   string candles = GetCandleSnapshot(sym, tf, entry_time, exit_time > 0 ? exit_time : TimeCurrent());

   // pip multiplier
   double mul = 10000.0;
   string sym_up = sym;
   StringToUpper(sym_up);
   if(StringFind(sym_up, "JPY") >= 0) mul = 100.0;
   if(StringFind(sym_up, "XAU") >= 0 || StringFind(sym_up, "XAG") >= 0) mul = 10.0;
   // crypto → mul=1 (بذار Flask محاسبه کنه)

   double sl_pips_val = sl_price > 0 ? MathAbs(entry_price - sl_price) * mul : 0;
   double tp_pips_val = tp_price > 0 ? MathAbs(tp_price - entry_price) * mul : 0;

   // exit_type
   string exit_type = (outcome == "loss") ? "sl" : "tp";

   // ساخت JSON
   string json = "{";
   json += "\"sym\":\""          + EscapeJSON(sym) + "\",";
   json += "\"tf\":\""           + tf_str + "\",";
   json += "\"direction\":\""    + direction + "\",";
   json += "\"entry\":"          + DoubleToString(entry_price, 5) + ",";
   json += "\"exit\":"           + (exit_price > 0 ? DoubleToString(exit_price, 5) : "null") + ",";
   json += "\"sl_price\":"       + (sl_price > 0 ? DoubleToString(sl_price, 5) : "null") + ",";
   json += "\"tp_price\":"       + (tp_price > 0 ? DoubleToString(tp_price, 5) : "null") + ",";
   json += "\"sl_pips\":"        + (sl_pips_val > 0 ? DoubleToString(sl_pips_val, 1) : "null") + ",";
   json += "\"tp_pips\":"        + (tp_pips_val > 0 ? DoubleToString(tp_pips_val, 1) : "null") + ",";
   json += "\"size\":"           + DoubleToString(lots, 2) + ",";
   json += "\"entryTime\":\""    + FormatTehran(entry_time) + "\",";
   json += "\"exitTime\":\""     + FormatTehran(exit_time) + "\",";
   json += "\"outcome\":\""      + outcome + "\",";
   json += "\"exit_type\":\""    + exit_type + "\",";
   json += "\"pnl\":"            + DoubleToString(total_profit, 2) + ",";
   json += "\"mt4_ticket\":"     + IntegerToString(deal_ticket) + ",";
   json += "\"mt4_position_id\":"+ IntegerToString(position_id) + ",";
   json += "\"mt4_lots\":"       + DoubleToString(lots, 2) + ",";
   json += "\"mt4_profit\":"     + DoubleToString(total_profit, 2) + ",";
   json += "\"note\":\""         + EscapeJSON(comment) + "\",";
   json += "\"exitNote\":\"MT5 deal #" + IntegerToString(deal_ticket) + "\",";
   json += "\"source\":\"mt5_ea\",";
   json += "\"candle_snapshot\":" + candles;
   json += "}";

   string response = "";
   bool ok = PostJSON("/api/mt4/test", json, response);

   if(ok)
      Print("[EA] ✅ #", deal_ticket, " ", sym, " ", direction, " ", outcome,
            " profit=", DoubleToString(total_profit, 2), " candles=", candles == "[]" ? 0 : 1);
   else
      Print("[EA] ❌ ارسال ناموفق: #", deal_ticket, " ", sym);

   return ok;
  }

//+------------------------------------------------------------------+
// تابع اصلی: جمع‌آوری و ارسال
//+------------------------------------------------------------------+
void CollectAndSend()
  {
   Print("=== CollectAndSend شروع شد — بازه: ", DAYS_BACK, " روز ===");
   UpdateStatus("در حال ارسال...", clrYellow);

   datetime from_time = TimeCurrent() - (datetime)(DAYS_BACK * 86400);
   datetime to_time   = TimeCurrent();

   // لود تاریخچه
   if(!HistorySelect(from_time, to_time))
     {
      Print("[EA] ❌ HistorySelect ناموفق");
      UpdateStatus("خطا: HistorySelect", clrRed);
      return;
     }

   int total = HistoryDealsTotal();
   Print("[EA] تعداد deal در بازه: ", total);

   int sent  = 0;
   int skip  = 0;
   int fail  = 0;

   for(int i = 0; i < total; i++)
     {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;

      // قبلاً ارسال شده؟
      if(IsAlreadySent(ticket)) { skip++; continue; }

      // سعی ارسال
      bool ok = SendDeal(ticket);

      if(ok)
        {
         MarkAsSent(ticket);
         sent++;
         Sleep(600); // throttle
        }
      else
        {
         // اگه false برگشت ولی خطا نبود (مثلاً deal خروج بود) skip حساب کن
         skip++;
        }
     }

   string status_msg = StringFormat("✅ ارسال: %d | skip: %d | خطا: %d", sent, skip, fail);
   Print("=== CollectAndSend تمام شد — ", status_msg, " ===");
   UpdateStatus(status_msg, sent > 0 ? clrLime : clrSilver);
   g_lastRun = TimeCurrent();
  }

//+------------------------------------------------------------------+
// رسم دکمه و label وضعیت روی چارت
//+------------------------------------------------------------------+
void DrawButton()
  {
   // دکمه ارسال
   string btn = "JC_SendBtn";
   ObjectDelete(0, btn);
   ObjectCreate(0, btn, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, btn, OBJPROP_XDISTANCE,  15);
   ObjectSetInteger(0, btn, OBJPROP_YDISTANCE,  30);
   ObjectSetInteger(0, btn, OBJPROP_XSIZE,      200);
   ObjectSetInteger(0, btn, OBJPROP_YSIZE,       38);
   ObjectSetString(0,  btn, OBJPROP_TEXT,   "📤  ارسال به Journal");
   ObjectSetInteger(0, btn, OBJPROP_COLOR,       clrWhite);
   ObjectSetInteger(0, btn, OBJPROP_BGCOLOR,     C'40,40,55');
   ObjectSetInteger(0, btn, OBJPROP_BORDER_COLOR,C'80,80,120');
   ObjectSetInteger(0, btn, OBJPROP_FONTSIZE,    11);
   ObjectSetInteger(0, btn, OBJPROP_CORNER,      CORNER_LEFT_UPPER);
   ObjectSetInteger(0, btn, OBJPROP_SELECTABLE,  false);

   // label وضعیت
   string lbl = "JC_StatusLbl";
   ObjectDelete(0, lbl);
   ObjectCreate(0, lbl, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, lbl, OBJPROP_XDISTANCE, 15);
   ObjectSetInteger(0, lbl, OBJPROP_YDISTANCE, 74);
   ObjectSetString(0,  lbl, OBJPROP_TEXT,  "آماده — روی دکمه کلیک کن");
   ObjectSetInteger(0, lbl, OBJPROP_COLOR, clrSilver);
   ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, lbl, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);

   ChartRedraw(0);
  }

void UpdateStatus(string msg, color clr)
  {
   ObjectSetString(0,  "JC_StatusLbl", OBJPROP_TEXT,  msg);
   ObjectSetInteger(0, "JC_StatusLbl", OBJPROP_COLOR, clr);
   ChartRedraw(0);
  }

//+------------------------------------------------------------------+
void OnChartEvent(const int id,
                  const long   &lparam,
                  const double &dparam,
                  const string &sparam)
  {
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == "JC_SendBtn")
     {
      ObjectSetInteger(0, "JC_SendBtn", OBJPROP_STATE, false); // reset pressed
      Print("[EA] دکمه کلیک شد — شروع ارسال دستی...");
      CollectAndSend();
     }
  }
//+------------------------------------------------------------------+
