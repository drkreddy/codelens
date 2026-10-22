package com.bank.payments.domain;

import com.bank.payments.infra.NpciUpiClient;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;

@Service
public class UpiPaymentService {
    private final CoreBankingPort core;
    private final NpciUpiClient npci;

    public UpiPaymentService(CoreBankingPort core, NpciUpiClient npci) {
        this.core = core;
        this.npci = npci;
    }

    public String pay(String account, String payeeVpa, long amountPaise) {
        core.debit(account, amountPaise);
        return npci.sendReqPay(payeeVpa, amountPaise);
    }

    @KafkaListener(topics = "upi.callback")
    public void onCallback(String respPay) {
        if (respPay.contains("FAILURE")) {
            core.credit("ACC-1", 100L);
        }
    }
}
