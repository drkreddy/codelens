package com.bank.payments.domain;

public interface CoreBankingPort {
    void debit(String account, long amountPaise);
    void credit(String account, long amountPaise);
}
