package com.bank.mobile.domain;

import org.springframework.stereotype.Service;

@Service
public class AccountService {
    private final CoreBankingGateway core;

    public AccountService(CoreBankingGateway core) { this.core = core; }

    public String balance(String accountId) {
        return core.getBalance(accountId);
    }
}
