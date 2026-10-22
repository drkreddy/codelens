package com.bank.mobile.domain;

import org.springframework.stereotype.Service;

@Service
public class KycService {
    private final AadhaarGateway aadhaar;

    public KycService(AadhaarGateway aadhaar) { this.aadhaar = aadhaar; }

    public boolean verify(String aadhaarRef) {
        return aadhaar.authenticate(aadhaarRef);
    }
}
