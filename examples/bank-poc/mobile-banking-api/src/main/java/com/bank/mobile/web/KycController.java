package com.bank.mobile.web;

import com.bank.mobile.domain.KycService;
import org.springframework.web.bind.annotation.*;

@RestController
public class KycController {
    private final KycService kyc;

    public KycController(KycService kyc) { this.kyc = kyc; }

    @PostMapping("/kyc/aadhaar")
    public boolean verify(@RequestBody String aadhaarRef) {
        return kyc.verify(aadhaarRef);
    }
}
