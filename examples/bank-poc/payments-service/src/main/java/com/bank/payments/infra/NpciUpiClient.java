package com.bank.payments.infra;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class NpciUpiClient {
    private final RestTemplate restTemplate = new RestTemplate();

    @Value("${npci.upi.url}")
    private String upiUrl;

    public String sendReqPay(String payeeVpa, long amountPaise) {
        String xml = "<upi:ReqPay xmlns:upi=\"http://npci.org/upi/schema/\"><Payee addr=\"" + payeeVpa + "\"/></upi:ReqPay>";
        return restTemplate.postForObject(upiUrl + "/ReqPay/2.0", xml, String.class);
    }
}
