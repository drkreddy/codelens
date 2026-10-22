package com.bank.payments.web;

import com.bank.payments.domain.UpiPaymentService;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/upi")
public class UpiController {
    private final UpiPaymentService upi;

    public UpiController(UpiPaymentService upi) { this.upi = upi; }

    @PostMapping("/pay")
    public String pay(@RequestBody String request) {
        return upi.pay("ACC-1", "merchant@bank", 100L);
    }
}
