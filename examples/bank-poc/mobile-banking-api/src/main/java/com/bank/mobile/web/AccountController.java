package com.bank.mobile.web;

import com.bank.mobile.domain.AccountService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/accounts")
public class AccountController {
    private final AccountService accounts;

    public AccountController(AccountService accounts) { this.accounts = accounts; }

    @GetMapping("/{id}/balance")
    public String balance(@PathVariable String id) {
        return accounts.balance(id);
    }
}
