//+------------------------------------------------------------------+
//|  JournalCollector.mq5  — نسخه 3.0                               |
//|  - تاریخ شروع/پایان دستی                                        |
//|  - فیلتر Magic Number                                            |
//|  - چند تایم‌فریم per magic                                       |
//|  - فقط دستی (دکمه) — بدون auto                                  |
//|  - duplicate با position_id                                      |
//|  - ترید باز → skip، وقتی بسته شد ارسال میشه                     |
//+------------------------------------------------------------------+
#property copyright "PriceAlert Journal"
#property version   "3.00"
#property strict

//===================================================================
// تنظیمات اصلی
//===================================================================
input string SERVER_URL     = "https://YOUR-APP.onrender.com";
input string API_SECRET     = "";

// بازه زمانی دستی (فرمت: YYYY.MM.DD)
input string FROM_DATE      = "2026.05.01";   // از این تاریخ
input string TO_DATE        = "2026.05.31";   // تا این تاریخ (خالی = امروز)

// تایم‌فریم پیش‌فرض (اگه magic number تنظیم نشده)
input string DEFAULT_TF     = "15m";

// فیلتر Magic Number — میتونی چند تا بذاری با کاما: "12345,67890,0"
// 0 = همه magic ها
input string MAGIC_FILTER   = "0";

// تنظیمات کندل
input int    CANDLES_BEFORE = 30;
input int    CANDLES_AFTER  = 30;

input bool   LOG_VERBOSE    = true;

//===================================================================
// Magic Number → Timeframe mapping
// فرمت: "magic1:tf1,magic2:tf2"  مثال: "12345:15m,67890:1h,11111:5m"
// اگه magic توی لیست نبود از DEFAULT_TF استفاده میشه
input string MAGIC_TF_MAP   = "";
// مثال: "100:15m,200:1h,300:5m"
//===================================================================

string g_sentFile = "JC_v3_sent.txt";

//+------------------------------------------------------------------+
int OnInit()
  {
   Print("=== JournalCollector v3.0 ===");
   Print("FROM: ", FROM_DATE, "  TO: ", TO_DATE == "" ? "امروز" : TO_DATE);
   Print("Magic filter: ", MAGIC_FILTER);
   Print("Default TF: ", DEFAULT_TF);
   DrawButton();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   ObjectDelete(0, "JC_SendBtn");
   ObjectDelete(0, "JC_StatusLbl");
  }

void OnTick() {} // فقط دستی — بدون auto

//+------------------------------------------------------------------+
// تبدیل "YYYY.MM.DD" به datetime
//+------------------------------------------------------------------+
datetime ParseDate(string s)
  {
   if(s == "") return TimeCurrent();
   // فرمت: 2026.05.01
   string parts[];
   int n = StringSplit(s, '.', parts);
   if(n < 3) return TimeCurrent();
   MqlDateTime mdt = {};
   mdt.year  = (int)StringToInteger(parts[0]);
   mdt.mon   = (int)StringToInteger(parts[1]);
   mdt.day   = (int)StringToInteger(parts[2]);
   mdt.hour  = 0; mdt.min = 0; mdt.sec = 0;
   return StructToTime(mdt);
  }

//+------------------------------------------------------------------+
// تبدیل string TF به ENUM
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES StrToTF(string tf)
  {
   if(tf=="1m"||tf=="M1")  return PERIOD_M1;
   if(tf=="5m"||tf=="M5")  return PERIOD_M5;
   if(tf=="15m"||tf=="M15")return PERIOD_M15;
   if(tf=="30m"||tf=="M30")return PERIOD_M30;
   if(tf=="1h"||tf=="H1")  return PERIOD_H1;
   if(tf=="4h"||tf=="H4")  return PERIOD_H4;
   if(tf=="1d"||tf=="D1")  return PERIOD_D1;
   return PERIOD_H1;
  }

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
// تایم‌فریم مناسب برای یه magic number
//+------------------------------------------------------------------+
string GetTFForMagic(long magic)
  {
   if(MAGIC_TF_MAP == "") return DEFAULT_TF;
   string pairs[];
   int n = StringSplit(MAGIC_TF_MAP, ',', pairs);
   for(int i = 0; i < n; i++)
     {
      string kv[];
      if(StringSplit(pairs[i], ':', kv) == 2)
        {
         if(StringToInteger(kv[0]) == magic)
            return kv[1];
        }
     }
   return DEFAULT_TF;
  }

//+------------------------------------------------------------------+
// چک magic filter
//+------------------------------------------------------------------+
bool MagicAllowed(long magic)
  {
   if(MAGIC_FILTER == "0" || MAGIC_FILTER == "") return true;
   string parts[];
   int n = StringSplit(MAGIC_FILTER, ',', parts);
   for(int i = 0; i < n; i++)
      if(StringToInteger(StringTrimLeft(StringTrimRight(parts[i]))) == magic)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
string FormatTehran(datetime utc)
  {
   datetime teh = utc + 3*3600 + 30*60;
   MqlDateTime m; TimeToStruct(teh, m);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",
                       m.year,m.mon,m.day,m.hour,m.min,m.sec);
  }

string EscJ(string s)
  {
   StringReplace(s,"\\","\\\\"); StringReplace(s,"\"","\\\"");
   StringReplace(s,"\n","\\n");  StringReplace(s,"\r","\\r");
   return s;
  }

string GetOutcome(double p)
  {
   if(p >  0.01) return "win";
   if(p < -0.01) return "loss";
   return "breakeven";
  }

//+------------------------------------------------------------------+
// کندل‌ها
//+------------------------------------------------------------------+
string GetCandles(string sym, ENUM_TIMEFRAMES tf, datetime entry, datetime exitt)
  {
   SymbolSelect(sym, true);
   datetime from = entry  - (datetime)(CANDLES_BEFORE * PeriodSeconds(tf));
   datetime to   = exitt  + (datetime)(CANDLES_AFTER  * PeriodSeconds(tf));
   if(to > TimeCurrent()) to = TimeCurrent();

   MqlRates r[]; ArraySetAsSeries(r, false);
   int n = CopyRates(sym, tf, from, to, r);
   if(n <= 0) { Print("[EA] CopyRates 0 — ", sym, " err=", GetLastError()); return "[]"; }

   string a = "[";
   for(int i = 0; i < n; i++)
     {
      if(i > 0) a += ",";
      a += StringFormat("{\"t\":%d,\"o\":%.5f,\"h\":%.5f,\"l\":%.5f,\"c\":%.5f,\"v\":%d}",
                        (long)r[i].time,r[i].open,r[i].high,r[i].low,r[i].close,(long)r[i].tick_volume);
     }
   return a + "]";
  }

//+------------------------------------------------------------------+
// HTTP POST با retry
//+------------------------------------------------------------------+
bool PostJSON(string endpoint, string body, string &resp)
  {
   string url  = SERVER_URL + endpoint;
   string hdrs = "Content-Type: application/json\r\n";
   if(StringLen(API_SECRET) > 0) hdrs += "X-API-Secret: " + API_SECRET + "\r\n";

   uchar pd[], rd[]; string rh;
   StringToCharArray(body, pd, 0, StringLen(body), CP_UTF8);
   int sz = ArraySize(pd);
   if(sz > 0 && pd[sz-1] == 0) ArrayResize(pd, sz-1);

   int res = -1;
   for(int attempt = 0; attempt < 2 && res == -1; attempt++)
     {
      if(attempt > 0) Sleep(2000);
      ResetLastError();
      res = WebRequest("POST", url, hdrs, 30000, pd, rd, rh);
     }

   if(res == -1)
     {
      int e = GetLastError();
      Print("[EA] WebRequest error: ", e);
      if(e == 4014) Print("[EA] URL رو در Tools>Options>Expert Advisors اضافه کن: ", SERVER_URL);
      return false;
     }
   resp = CharArrayToString(rd, 0, WHOLE_ARRAY, CP_UTF8);
   if(res != 200 && res != 201) { Print("[EA] HTTP ", res, ": ", StringSubstr(resp,0,150)); return false; }
   if(LOG_VERBOSE) Print("[EA] POST OK → ", StringSubstr(resp,0,100));
   return true;
  }

//+------------------------------------------------------------------+
// duplicate check
//+------------------------------------------------------------------+
bool IsSent(long pid)
  {
   int h = FileOpen(g_sentFile, FILE_READ|FILE_TXT|FILE_SHARE_READ|FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   string c = "";
   while(!FileIsEnding(h)) c += FileReadString(h);
   FileClose(h);
   return StringFind(c, "|" + IntegerToString(pid) + "|") >= 0;
  }

void MarkSent(long pid)
  {
   int h = FileOpen(g_sentFile, FILE_READ|FILE_WRITE|FILE_TXT|FILE_SHARE_READ|FILE_ANSI);
   if(h == INVALID_HANDLE) h = FileOpen(g_sentFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, "|" + IntegerToString(pid) + "|");
   FileClose(h);
  }

//+------------------------------------------------------------------+
// ارسال یه position
//+------------------------------------------------------------------+
bool SendPosition(long pos_id, int total_deals)
  {
   string sym = ""; string direction = "";
   double entry_price=0, exit_price=0, lots=0, total_profit=0;
   double sl_price=0, tp_price=0;
   datetime entry_time=0, exit_time=0;
   string comment="";
   ulong entry_ticket=0, exit_ticket=0;
   long magic=0;
   bool found_exit = false;

   for(int i=0; i<total_deals; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if((long)HistoryDealGetInteger(d,DEAL_POSITION_ID) != pos_id) continue;

      ENUM_DEAL_TYPE  dt = (ENUM_DEAL_TYPE) HistoryDealGetInteger(d,DEAL_TYPE);
      ENUM_DEAL_ENTRY de = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(d,DEAL_ENTRY);
      if(dt!=DEAL_TYPE_BUY && dt!=DEAL_TYPE_SELL) continue;

      if(de == DEAL_ENTRY_IN)
        {
         entry_ticket  = d;
         sym           = HistoryDealGetString(d,DEAL_SYMBOL);
         direction     = (dt==DEAL_TYPE_BUY)?"BUY":"SELL";
         entry_price   = HistoryDealGetDouble(d,DEAL_PRICE);
         entry_time    = (datetime)HistoryDealGetInteger(d,DEAL_TIME);
         lots          = HistoryDealGetDouble(d,DEAL_VOLUME);
         comment       = HistoryDealGetString(d,DEAL_COMMENT);
         magic         = HistoryDealGetInteger(d,DEAL_MAGIC);
         // SL/TP از deal ورود (Lightfinance)
         double d_sl=HistoryDealGetDouble(d,DEAL_SL);
         double d_tp=HistoryDealGetDouble(d,DEAL_TP);
         if(d_sl>0) sl_price=d_sl;
         if(d_tp>0) tp_price=d_tp;
        }
      else if(de==DEAL_ENTRY_OUT || de==DEAL_ENTRY_INOUT)
        {
         exit_ticket  = d;
         exit_price   = HistoryDealGetDouble(d,DEAL_PRICE);
         exit_time    = (datetime)HistoryDealGetInteger(d,DEAL_TIME);
         total_profit += HistoryDealGetDouble(d,DEAL_PROFIT)
                       + HistoryDealGetDouble(d,DEAL_SWAP)
                       + HistoryDealGetDouble(d,DEAL_COMMISSION);
         // SL/TP fallback از deal خروج
         double d_sl2=HistoryDealGetDouble(d,DEAL_SL);
         double d_tp2=HistoryDealGetDouble(d,DEAL_TP);
         if(d_sl2>0 && sl_price==0) sl_price=d_sl2;
         if(d_tp2>0 && tp_price==0) tp_price=d_tp2;
         found_exit = true;
        }
     }

   // ترید باز → skip (بعداً که بسته شد میاد)
   if(!found_exit)
     {
      if(LOG_VERBOSE) Print("[EA] pos=",pos_id," باز است — skip");
      return false;
     }

   if(sym=="" || entry_price==0) return false;

   // فیلتر magic
   if(!MagicAllowed(magic))
     {
      if(LOG_VERBOSE) Print("[EA] pos=",pos_id," magic=",magic," فیلتر شد");
      return false;
     }

   // تایم‌فریم براساس magic
   string tf_str    = GetTFForMagic(magic);
   ENUM_TIMEFRAMES tf = StrToTF(tf_str);

   string outcome  = GetOutcome(total_profit);
   string exit_type= (outcome=="loss")?"sl":"tp";

   double mul=10000.0;
   string su=sym; StringToUpper(su);
   if(StringFind(su,"JPY")>=0) mul=100.0;
   if(StringFind(su,"XAU")>=0||StringFind(su,"XAG")>=0) mul=10.0;

   double sl_pips = sl_price>0 ? MathAbs(entry_price-sl_price)*mul : 0;
   double tp_pips = tp_price>0 ? MathAbs(tp_price-entry_price)*mul : 0;

   string candles = GetCandles(sym, tf, entry_time, exit_time);

   string json="{";
   json+="\"sym\":\""          +EscJ(sym)+"\",";
   json+="\"tf\":\""           +tf_str+"\",";
   json+="\"direction\":\""    +direction+"\",";
   json+="\"entry\":"          +DoubleToString(entry_price,5)+",";
   json+="\"exit\":"           +DoubleToString(exit_price,5)+",";
   json+="\"sl_price\":"       +(sl_price>0?DoubleToString(sl_price,5):"null")+",";
   json+="\"tp_price\":"       +(tp_price>0?DoubleToString(tp_price,5):"null")+",";
   json+="\"sl_pips\":"        +(sl_pips>0?DoubleToString(sl_pips,1):"null")+",";
   json+="\"tp_pips\":"        +(tp_pips>0?DoubleToString(tp_pips,1):"null")+",";
   json+="\"size\":"           +DoubleToString(lots,2)+",";
   json+="\"entryTime\":\""    +FormatTehran(entry_time)+"\",";
   json+="\"exitTime\":\""     +FormatTehran(exit_time)+"\",";
   json+="\"outcome\":\""      +outcome+"\",";
   json+="\"exit_type\":\""    +exit_type+"\",";
   json+="\"pnl\":"            +DoubleToString(total_profit,2)+",";
   json+="\"mt4_ticket\":"     +IntegerToString(entry_ticket)+",";
   json+="\"mt4_position_id\":"+IntegerToString(pos_id)+",";
   json+="\"mt4_magic\":"      +IntegerToString(magic)+",";
   json+="\"mt4_profit\":"     +DoubleToString(total_profit,2)+",";
   json+="\"note\":\""         +EscJ(comment)+"\",";
   json+="\"source\":\"mt5_ea\",";
   json+="\"candle_snapshot\":"+candles;
   json+="}";

   string resp="";
   bool ok = PostJSON("/api/journal/mt4", json, resp);
   if(ok)
      Print("[EA] OK pos=",pos_id," ",sym," ",direction," ",outcome,
            " magic=",magic," tf=",tf_str," profit=",DoubleToString(total_profit,2));
   else
      Print("[EA] FAIL pos=",pos_id," ",sym);
   return ok;
  }

//+------------------------------------------------------------------+
// تابع اصلی
//+------------------------------------------------------------------+
void CollectAndSend()
  {
   datetime from = ParseDate(FROM_DATE);
   datetime to   = (TO_DATE == "") ? TimeCurrent() : ParseDate(TO_DATE) + 86400; // آخر روز

   Print("=== CollectAndSend ===  از ", FROM_DATE, " تا ", TO_DATE==""?"امروز":TO_DATE);
   UpdateStatus("در حال ارسال...", clrYellow);

   if(!HistorySelect(from, to))
     {
      Print("[EA] HistorySelect ناموفق");
      UpdateStatus("خطا: HistorySelect", clrRed);
      return;
     }

   int total_deals = HistoryDealsTotal();
   Print("[EA] deal در بازه: ", total_deals);

   // جمع‌آوری position_id های یکتا
   long positions[];
   int  pos_count=0;
   for(int i=0; i<total_deals; i++)
     {
      ulong d   = HistoryDealGetTicket(i);
      long  pid = HistoryDealGetInteger(d, DEAL_POSITION_ID);
      if(pid==0) continue;
      bool dup=false;
      for(int x=0;x<pos_count;x++) if(positions[x]==pid){dup=true;break;}
      if(dup) continue;
      ArrayResize(positions, pos_count+1);
      positions[pos_count++]=pid;
     }

   Print("[EA] position یکتا: ", pos_count);
   int sent=0, skip=0, fail=0;

   for(int i=0; i<pos_count; i++)
     {
      long pid = positions[i];
      if(IsSent(pid)) { skip++; continue; }
      bool ok = SendPosition(pid, total_deals);
      if(ok)       { MarkSent(pid); sent++; Sleep(600); }
      else           skip++;
     }

   string msg = StringFormat("OK:%d | skip:%d | fail:%d", sent, skip, fail);
   Print("=== تمام — ", msg, " ===");
   UpdateStatus(msg, sent>0?clrLime:clrSilver);
  }

//+------------------------------------------------------------------+
void DrawButton()
  {
   string b="JC_SendBtn";
   ObjectDelete(0,b);
   ObjectCreate(0,b,OBJ_BUTTON,0,0,0);
   ObjectSetInteger(0,b,OBJPROP_XDISTANCE,15);
   ObjectSetInteger(0,b,OBJPROP_YDISTANCE,30);
   ObjectSetInteger(0,b,OBJPROP_XSIZE,220);
   ObjectSetInteger(0,b,OBJPROP_YSIZE,36);
   ObjectSetString(0,b,OBJPROP_TEXT,"📤  ارسال به Journal  [" + FROM_DATE + "]");
   ObjectSetInteger(0,b,OBJPROP_COLOR,clrWhite);
   ObjectSetInteger(0,b,OBJPROP_BGCOLOR,C'40,40,55');
   ObjectSetInteger(0,b,OBJPROP_FONTSIZE,9);
   ObjectSetInteger(0,b,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,b,OBJPROP_SELECTABLE,false);

   string l="JC_StatusLbl";
   ObjectDelete(0,l);
   ObjectCreate(0,l,OBJ_LABEL,0,0,0);
   ObjectSetInteger(0,l,OBJPROP_XDISTANCE,15);
   ObjectSetInteger(0,l,OBJPROP_YDISTANCE,72);
   ObjectSetString(0,l,OBJPROP_TEXT,"آماده — دکمه رو بزن");
   ObjectSetInteger(0,l,OBJPROP_COLOR,clrSilver);
   ObjectSetInteger(0,l,OBJPROP_FONTSIZE,9);
   ObjectSetInteger(0,l,OBJPROP_CORNER,CORNER_LEFT_UPPER);
   ObjectSetInteger(0,l,OBJPROP_SELECTABLE,false);
   ChartRedraw(0);
  }

void UpdateStatus(string msg, color clr)
  {
   ObjectSetString(0,"JC_StatusLbl",OBJPROP_TEXT,msg);
   ObjectSetInteger(0,"JC_StatusLbl",OBJPROP_COLOR,clr);
   ChartRedraw(0);
  }

void OnChartEvent(const int id,const long &lp,const double &dp,const string &sp)
  {
   if(id==CHARTEVENT_OBJECT_CLICK && sp=="JC_SendBtn")
     {
      ObjectSetInteger(0,"JC_SendBtn",OBJPROP_STATE,false);
      Print("[EA] دکمه کلیک — ارسال...");
      CollectAndSend();
     }
  }
//+------------------------------------------------------------------+
