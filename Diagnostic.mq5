//+------------------------------------------------------------------+
//| Diagnostic.mq5 — فقط لاگ raw data یه position                   |
//+------------------------------------------------------------------+
#property version "1.00"
#property strict

input int DAYS_BACK = 7;

int OnInit()
  {
   Sleep(1000);
   RunDiag();
   return INIT_SUCCEEDED;
  }

void OnTick() {}

void RunDiag()
  {
   datetime from = TimeCurrent() - (datetime)(DAYS_BACK * 86400);
   HistorySelect(from, TimeCurrent());

   int total_deals = HistoryDealsTotal();
   Print("=== DIAGNOSTIC === deals=", total_deals);

   // فقط اولین position بسته‌شده رو نشون بده
   long first_pos = -1;
   for(int i = 0; i < total_deals; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      ENUM_DEAL_ENTRY de = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY);
      ENUM_DEAL_TYPE  dt = (ENUM_DEAL_TYPE) HistoryDealGetInteger(d, DEAL_TYPE);
      if(dt != DEAL_TYPE_BUY && dt != DEAL_TYPE_SELL) continue;
      if(de == DEAL_ENTRY_IN)
        {
         first_pos = HistoryDealGetInteger(d, DEAL_POSITION_ID);
         break;
        }
     }

   if(first_pos < 0) { Print("هیچ position ای پیدا نشد"); return; }
   Print("--- position_id=", first_pos, " ---");

   // همه deal های این position
   Print("=== DEALS ===");
   for(int i = 0; i < total_deals; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(HistoryDealGetInteger(d, DEAL_POSITION_ID) != first_pos) continue;
      Print("  deal #", d,
            " type=",  EnumToString((ENUM_DEAL_TYPE) HistoryDealGetInteger(d,DEAL_TYPE)),
            " entry=", EnumToString((ENUM_DEAL_ENTRY)HistoryDealGetInteger(d,DEAL_ENTRY)),
            " price=", HistoryDealGetDouble(d, DEAL_PRICE),
            " SL=",    HistoryDealGetDouble(d, DEAL_SL),
            " TP=",    HistoryDealGetDouble(d, DEAL_TP),
            " profit=",HistoryDealGetDouble(d, DEAL_PROFIT),
            " sym=",   HistoryDealGetString(d, DEAL_SYMBOL),
            " comment=",HistoryDealGetString(d,DEAL_COMMENT));
     }

   // همه order های این position
   Print("=== ORDERS ===");
   int total_orders = HistoryOrdersTotal();
   for(int k = 0; k < total_orders; k++)
     {
      ulong ord = HistoryOrderGetTicket(k);
      if(HistoryOrderGetInteger(ord, ORDER_POSITION_ID) != first_pos) continue;
      Print("  order #", ord,
            " type=",  EnumToString((ENUM_ORDER_TYPE)HistoryOrderGetInteger(ord,ORDER_TYPE)),
            " price=", HistoryOrderGetDouble(ord, ORDER_PRICE_OPEN),
            " SL=",    HistoryOrderGetDouble(ord, ORDER_SL),
            " TP=",    HistoryOrderGetDouble(ord, ORDER_TP),
            " state=", EnumToString((ENUM_ORDER_STATE)HistoryOrderGetInteger(ord,ORDER_STATE)),
            " comment=",HistoryOrderGetString(ord,ORDER_COMMENT));
     }
   Print("=== END ===");
  }
